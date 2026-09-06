"""Pure unit tests for ``nethackers doctor``'s diagnostics core
(``diagnostics.py``): ``run_checks``/``capability_ready``/``exit_code``/
``to_json``. Every probe ``run_checks`` uses is injected as a fake here --
no real docker/network call is ever made by this file.

``capability_ready``/``exit_code`` (the pure fold) are also table-tested
directly against hand-built ``CheckResult`` lists at the bottom, independent
of ``run_checks`` -- that's the exhaustive hard-fail/soft-warn/soft-fail x
capability matrix spec S5.6/INV6 describes.

See ``tests/test_doctor_cli.py`` for the CLI wiring (``cli.main(["doctor",
...])``), which fakes ``cli.run_checks`` wholesale rather than these
lower-level probes.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import nethackers.diagnostics as diagnostics
from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import (
    CAPABILITIES,
    CHECK_SPECS,
    CheckResult,
    _short_digest,
    capability_ready,
    exit_code,
    render_human,
    render_plain,
    run_checks,
    to_json,
)
from nethackers.hubclient.client import HubUnreachable
from nethackers.hubclient.credentials import Credentials

_HEX64 = "a" * 64


def _runtime_ok(runtime="docker"):
    return lambda: RuntimeReport(runtime, (RuntimeCandidate(runtime, "usable", ""),))


def _runtime_none():
    return RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                               RuntimeCandidate("podman", "absent", "")))


def _no_64_hex_run(text: str) -> bool:
    return re.search(r"[0-9a-f]{64}", text) is None


# --- run_checks: fakes for every injectable probe, all "healthy" by default -


def _ref(explicit, kind):
    # Mimics resolve_image's real shape (a GHCR digest-pin ref) closely enough
    # for the "@sha256:" branch in _check_image's fix-text selection to exercise
    # the pin path, not the local-dev-tag path.
    return f"ghcr.io/dunnolab/nethackers-{kind}@sha256:{_HEX64}"


def _unreachable_hub(hub):
    raise HubUnreachable(hub)


def _healthy_kwargs(**overrides):
    kwargs = dict(
        operator="claude",
        hub="https://example.invalid",
        runtime_report=_runtime_ok(),
        resolve_image=_ref,
        image_present=lambda ref: True,
        manifest_reachable=lambda ref: True,
        # "not a repo checkout" is the realistic default for the installed
        # (non-repo) users this feature targets, and keeps this file fully
        # hermetic -- without an explicit fake here, the unreachable-image
        # branch would fall back to run_checks' own real `_repo_root`, which
        # walks the real filesystem (and would find THIS repo's own
        # Dockerfile.mutator/Makefile, since the suite runs from a checkout).
        repo_root=lambda: None,
        preflight_operator=lambda operator: None,
        hub_mode=lambda hub: "github",
        load_creds=lambda: Credentials("castiel", "tok"),
        gh_state=lambda: ("castiel", "authed"),
    )
    kwargs.update(overrides)
    return kwargs


def test_run_checks_returns_one_result_per_check_id():
    results = run_checks(**_healthy_kwargs())
    ids = {r.id for r in results}
    assert ids == {
        "container_runtime", "arena_image", "mutator_image",
        "hub", "hub_login", "gh", "operator",
    }


def test_all_ok_every_capability_ready_and_exit_zero():
    results = run_checks(**_healthy_kwargs())
    for cap in CAPABILITIES:
        assert capability_ready(results, cap) is True, cap
        assert exit_code(results, cap) == 0, cap
    assert exit_code(results, None) == 0


def test_everything_down_gates_every_capability():
    results = run_checks(**_healthy_kwargs(
        runtime_report=_runtime_none,
        image_present=lambda ref: False,
        manifest_reachable=lambda ref: False,
        preflight_operator=lambda operator: "not logged in",
        hub_mode=_unreachable_hub,
        load_creds=lambda: None,
        gh_state=lambda: (None, "missing"),
    ))
    assert exit_code(results, None) == 1
    for cap in CAPABILITIES:
        assert capability_ready(results, cap) is False, cap


def test_container_runtime_fail_surfaces_the_broken_binarys_actual_error():
    # issue #50: a present-but-`info`-failed runtime (e.g. docker installed but
    # the daemon socket denies this user) must show the REAL cause and a
    # per-CLI breakdown, not a bare "no working container runtime found".
    report = RuntimeReport(None, (
        RuntimeCandidate("docker", "broken",
                         "permission denied while trying to connect to the Docker daemon socket"),
        RuntimeCandidate("podman", "absent", ""),
    ))
    results = run_checks(**_healthy_kwargs(runtime_report=lambda: report))
    cr = next(r for r in results if r.id == "container_runtime")
    assert cr.status == "fail"
    assert "permission denied" in cr.detail  # the actual error, flattened for -o json
    by_label = {it.label: it for it in cr.items}
    assert by_label["docker"].status == "fail"
    assert "permission denied" in by_label["docker"].detail
    assert by_label["podman"].status == "warn" and by_label["podman"].detail == "not installed"


def test_gh_unauthed_only_gates_publish_not_eval():
    results = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "unauthed")))
    assert exit_code(results, None) == 0  # eval still ready (the bare default)
    caps = {cap: capability_ready(results, cap) for cap in CAPABILITIES}
    assert caps == {"eval": True, "evolve": True, "publish": False, "browse": True}


def test_only_filters_results_to_exactly_the_requested_check_ids():
    results = run_checks(**_healthy_kwargs(only={"container_runtime", "arena_image"}))
    assert {r.id for r in results} == {"container_runtime", "arena_image"}


def test_only_none_default_is_unchanged_and_runs_all_seven():
    # Backward compatibility is the whole point of `only`: every existing
    # caller (the `doctor` CLI, this file's own healthy-path tests above)
    # omits it, and must see byte-for-byte the same 7-check behavior as
    # before `only` existed.
    results = run_checks(**_healthy_kwargs())
    assert {r.id for r in results} == set(CHECK_SPECS)


def test_only_never_invokes_the_excluded_checks_probes():
    """The regression guard: `only` is not a result-list filter bolted on
    after the fact -- the excluded checks' own probes must never be called at
    all. This is the exact contract the TUI evolve-readiness strip depends on
    (spec 5.8, "no round-trip on navigation"): scoping to the 4 local evolve
    checks must mean the `hub` HTTPS call / `gh` subprocess / creds-file read
    never fire on mount, not just that their results get hidden from
    display. Spies record every call AND raise if invoked -- either signal
    (a nonzero count, or the extra id leaking into the result set) would
    catch a regression even though `_safe` would otherwise swallow the raise
    into an ordinary "fail" CheckResult."""
    calls = {"hub_mode": 0, "gh_state": 0, "load_creds": 0}

    def _spy_hub_mode(hub):
        calls["hub_mode"] += 1
        raise AssertionError("hub_mode must not be called when 'hub' is filtered out")

    def _spy_gh_state():
        calls["gh_state"] += 1
        raise AssertionError("gh_state must not be called when 'gh' is filtered out")

    def _spy_load_creds():
        calls["load_creds"] += 1
        raise AssertionError("load_creds must not be called when 'hub_login' is filtered out")

    results = run_checks(**_healthy_kwargs(
        only={"container_runtime", "arena_image"},
        hub_mode=_spy_hub_mode, gh_state=_spy_gh_state, load_creds=_spy_load_creds,
    ))

    assert {r.id for r in results} == {"container_runtime", "arena_image"}
    assert calls == {"hub_mode": 0, "gh_state": 0, "load_creds": 0}


def test_only_with_operator_but_not_mutator_image_treats_mutator_as_absent():
    # Brief's own general-case callout: if `operator` is requested without
    # `mutator_image` also being in `only`, `_check_operator`'s
    # `mutator_present` must not reference an unbuilt/skipped result -- it
    # should behave as if the mutator image is absent (the safe default),
    # not crash or silently reuse a stale value.
    results = run_checks(**_healthy_kwargs(
        only={"operator"}, preflight_operator=lambda operator: "not logged in"))
    assert {r.id for r in results} == {"operator"}
    op = next(r for r in results if r.id == "operator")
    assert "sandbox" in op.fix   # the mutator-image-absent fix wording, not the plain-login one


def test_a_raising_probe_becomes_a_failed_check_not_an_exception():
    def _boom():
        raise RuntimeError("docker vanished")

    results = run_checks(**_healthy_kwargs(runtime_report=_boom))
    by_id = {r.id: r for r in results}
    assert by_id["container_runtime"].status == "fail"
    # the crash-path CheckResult still carries the RIGHT severity/capabilities
    # for its id -- these come from CHECK_SPECS on the _safe(...) fallback
    # path too, not just the happy-path builder, so a crash never silently
    # under- or over-gates a capability.
    assert by_id["container_runtime"].severity == CHECK_SPECS["container_runtime"][0]
    assert by_id["container_runtime"].capabilities == CHECK_SPECS["container_runtime"][1]
    # the crash is isolated to its own check -- every other check still
    # computed normally, not skipped/short-circuited.
    assert by_id["arena_image"].status == "ok"
    assert by_id["operator"].status == "ok"


# --- CHECK_SPECS: the single source for each check's (severity, capabilities)


def test_check_specs_covers_exactly_the_seven_check_ids():
    assert set(CHECK_SPECS) == {
        "container_runtime", "arena_image", "mutator_image",
        "hub", "hub_login", "gh", "operator",
    }


def test_run_checks_ids_match_check_specs():
    results = run_checks(**_healthy_kwargs())
    assert {r.id for r in results} == set(CHECK_SPECS)


def test_run_checks_severity_and_capabilities_always_match_check_specs():
    # Not just the crash path (above) -- the happy-path builders must also
    # never drift from CHECK_SPECS, for every check, in a normal run.
    results = run_checks(**_healthy_kwargs())
    for r in results:
        severity, caps = CHECK_SPECS[r.id]
        assert r.severity == severity, r.id
        assert r.capabilities == caps, r.id


# --- operator: "notes the other" unchecked operator (spec S5.6) ------------


def test_operator_checks_all_registered_agents_by_default():
    # operator=None (the default) probes EVERY registered coding agent, not one
    # + "note the other", and lists each agent's status.
    from nethackers.operators import OPERATORS
    seen = []
    results = run_checks(**_healthy_kwargs(
        operator=None, preflight_operator=lambda op: seen.append(op) or None))
    op = next(r for r in results if r.id == "operator")
    assert sorted(seen) == sorted(OPERATORS)          # every agent probed
    assert op.status == "ok"
    for agent in OPERATORS:
        assert agent in op.detail                     # each agent's status shown


def test_operator_ready_when_at_least_one_agent_logged_in():
    # evolve uses ONE operator (picked at Start) -> ready if >=1 is logged in.
    partial = run_checks(**_healthy_kwargs(
        operator=None,
        preflight_operator=lambda op: None if op == "claude" else "not logged in"))
    op = next(r for r in partial if r.id == "operator")
    assert op.status == "ok" and "claude" in op.detail
    # a fully logged-out host fails, naming how to log in
    out = run_checks(**_healthy_kwargs(
        operator=None, preflight_operator=lambda op: "not logged in"))
    op2 = next(r for r in out if r.id == "operator")
    assert op2.status == "fail" and "login" in (op2.fix or "")


def test_operator_narrows_to_a_single_named_agent():
    seen = []
    results = run_checks(**_healthy_kwargs(
        operator="codex", preflight_operator=lambda op: seen.append(op) or None))
    op = next(r for r in results if r.id == "operator")
    assert seen == ["codex"]                           # ONLY codex probed
    assert "codex" in op.detail and "claude" not in op.detail


def test_operator_items_are_per_agent_with_flat_detail_for_json():
    from nethackers.operators import OPERATORS
    results = run_checks(**_healthy_kwargs(
        operator=None,
        preflight_operator=lambda op: None if op == "codex" else "not logged in"))
    op = next(r for r in results if r.id == "operator")
    assert [it.label for it in op.items] == list(OPERATORS)   # one sub-item per agent
    by = {it.label: it for it in op.items}
    assert by["codex"].status == "ok" and by["codex"].detail == "logged in"
    assert by["claude"].status == "fail" and by["claude"].detail == "not logged in"
    # the same data stays flattened in `detail` -- what -o json / the schema use
    assert "codex: logged in" in op.detail and "claude: not logged in" in op.detail


def test_render_plain_renders_a_check_with_items_as_an_indented_sublist():
    from nethackers.diagnostics import CheckItem, render_plain
    results = [CheckResult(
        id="operator", status="ok", severity="hard",
        detail="codex: logged in, claude: not logged in", fix=None,
        capabilities=("evolve",),
        items=(CheckItem("codex", "ok", "logged in"),
               CheckItem("claude", "fail", "not logged in")))]
    lines = render_plain(results).splitlines()
    parent = next(ln for ln in lines if ln.rstrip().endswith("operator"))
    codex = next(ln for ln in lines if "codex" in ln)
    claude = next(ln for ln in lines if "claude" in ln)

    def indent(s: str) -> int:
        return len(s) - len(s.lstrip())

    assert indent(codex) > indent(parent) and indent(claude) > indent(parent)
    assert "[OK]" in codex and "logged in" in codex
    assert "[FAIL]" in claude and "not logged in" in claude


# --- hub: the default reads the EFFECTIVE stage, not a frozen prod literal -


def test_run_checks_hub_default_reads_effective_stage(monkeypatch):
    fake_stage = SimpleNamespace(hub_url="http://effective.example")
    monkeypatch.setattr(diagnostics, "load_stage", lambda: fake_stage)
    seen = {}

    def _capture_hub(hub):
        seen["hub"] = hub
        return "github"

    kwargs = _healthy_kwargs(hub_mode=_capture_hub)
    kwargs["hub"] = None  # the new sentinel default -- resolve via load_stage()

    run_checks(**kwargs)

    assert seen["hub"] == "http://effective.example"


def test_run_checks_explicit_hub_never_consults_load_stage(monkeypatch):
    def _boom():
        raise AssertionError("load_stage() should not be called when hub= is given")

    monkeypatch.setattr(diagnostics, "load_stage", _boom)
    run_checks(**_healthy_kwargs(hub="https://example.invalid"))  # must not raise


# --- image "unreachable" fix text: branches on repo presence, not ref shape


def test_image_unreachable_fix_suggests_make_inside_a_repo_checkout():
    results = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=lambda: Path("/fake/repo"),
    ))
    arena = next(r for r in results if r.id == "arena_image")
    assert arena.status == "fail"
    assert "make arena" in arena.fix
    assert "NETHACKERS_" not in arena.fix


def test_image_unreachable_fix_suggests_network_override_outside_a_repo():
    results = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=lambda: None,
    ))
    arena = next(r for r in results if r.id == "arena_image")
    assert arena.status == "fail"
    assert "make" not in arena.fix
    assert "NETHACKERS_ARENA_IMAGE" in arena.fix


# --- digest shortening: display-only, JSON stays sacrosanct ----------------


def test_short_digest_shortens_embedded_digest_and_keeps_surrounding_text():
    text = f"present — ghcr.io/dunnolab/nethackers-arena@sha256:{_HEX64}"
    shortened = _short_digest(text)

    assert _no_64_hex_run(shortened)
    assert shortened.startswith("present — ghcr.io/dunnolab/nethackers-arena@sha256:")
    assert f"@sha256:{_HEX64[:19]}…" in shortened
    assert shortened != text  # something actually changed


def _image_check_result(detail: str) -> CheckResult:
    return CheckResult(id="arena_image", status="ok", severity="hard", detail=detail,
                       fix=None, capabilities=("eval", "evolve"))


def test_render_human_and_plain_shorten_the_digest_in_detail():
    detail = f"present — ghcr.io/dunnolab/nethackers-arena@sha256:{_HEX64}"
    results = [_image_check_result(detail)]

    assert _no_64_hex_run(render_human(results))
    assert _no_64_hex_run(render_plain(results))


def test_to_json_keeps_the_full_unshortened_digest():
    detail = f"present — ghcr.io/dunnolab/nethackers-arena@sha256:{_HEX64}"
    results = [_image_check_result(detail)]

    data = to_json(results)

    assert _HEX64 in data["checks"][0]["detail"]
    assert data["checks"][0]["detail"] == detail  # byte-for-byte, not just "contains"


def test_gh_three_states_get_distinct_fixes():
    missing = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "missing")))
    unauthed = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "unauthed")))
    authed = run_checks(**_healthy_kwargs(gh_state=lambda: ("castiel", "authed")))
    gh_missing = next(r for r in missing if r.id == "gh")
    gh_unauthed = next(r for r in unauthed if r.id == "gh")
    gh_authed = next(r for r in authed if r.id == "gh")

    assert gh_missing.status == "fail" and "install" in gh_missing.fix.lower()
    assert gh_unauthed.status == "fail" and "gh auth login" in gh_unauthed.fix
    assert gh_authed.status == "ok" and gh_authed.fix is None
    # never collapse "not installed" and "installed but unauthed" into the
    # same message (spec S5.6) -- distinct fix text for distinct fixes.
    assert gh_missing.fix != gh_unauthed.fix


def test_operator_check_uses_the_given_operator_name():
    seen = []
    results = run_checks(**_healthy_kwargs(
        operator="codex",
        preflight_operator=lambda operator: seen.append(operator) or "nope",
    ))
    assert seen == ["codex"]
    op = next(r for r in results if r.id == "operator")
    assert op.status == "fail" and op.severity == "hard" and "codex" in op.fix


def test_image_present_vs_pullable_vs_unreachable_statuses():
    present = run_checks(**_healthy_kwargs())
    pullable = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: True))
    unreachable = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False))

    assert next(r for r in present if r.id == "arena_image").status == "ok"
    assert next(r for r in pullable if r.id == "arena_image").status == "warn"
    assert next(r for r in unreachable if r.id == "arena_image").status == "fail"
    # pullable/unreachable are still HARD -> not capability-ready, even though
    # "pullable" isn't a hard failure exactly (docs: needs `--pull` first).
    assert capability_ready(pullable, "eval") is False
    assert capability_ready(unreachable, "eval") is False


def test_hub_check_ok_and_unreachable():
    ok = run_checks(**_healthy_kwargs())
    down = run_checks(**_healthy_kwargs(hub_mode=_unreachable_hub))

    assert next(r for r in ok if r.id == "hub").status == "ok"
    hub_fail = next(r for r in down if r.id == "hub")
    assert hub_fail.status == "fail" and hub_fail.severity == "soft"
    assert capability_ready(down, "browse") is False
    assert capability_ready(down, "eval") is True  # hub unreachable never touches eval


def test_hub_login_check_ok_and_not_logged_in():
    ok = run_checks(**_healthy_kwargs())
    out = run_checks(**_healthy_kwargs(load_creds=lambda: None))

    assert next(r for r in ok if r.id == "hub_login").status == "ok"
    hl = next(r for r in out if r.id == "hub_login")
    assert hl.status == "fail" and "login" in hl.fix
    assert capability_ready(out, "publish") is False


def test_to_json_shape():
    results = run_checks(**_healthy_kwargs())
    data = to_json(results)

    assert set(data) == {"checks", "capabilities", "env"}
    assert len(data["checks"]) == len(results)
    assert data["capabilities"] == dict.fromkeys(CAPABILITIES, True)
    assert isinstance(data["checks"][0]["capabilities"], list)  # JSON-safe, not a bare tuple
    env = data["env"]
    assert {"nethackers", "run_schema_version", "images", "os", "arch", "python"} <= set(env)


# --- exit_code / capability_ready: the pure fold, EXHAUSTIVELY table-tested -
#
# Independent of run_checks -- hand-built CheckResult lists, exercising the
# hard-vs-soft contract directly: a HARD check gates every capability it's
# tagged with, full stop. A capability with NO hard checks of its own
# (publish/browse, in the real 7-check table) falls back to its own soft
# checks, where only "fail" gates it -- "warn" never gates ANY capability,
# hard- or soft-only alike (INV6: "soft warnings never flip it").
#
# Every check starts "ok" (via `_ok_results`, built FROM `CHECK_SPECS` --
# never a hand-copied second literal of the same id -> (severity,
# capabilities) table, so a future CHECK_SPECS change is automatically
# reflected here instead of leaving a stale duplicate). Each named scenario
# below flips only the checks it names via `_flip`; every other check stays
# "ok", isolating exactly the behavior the scenario is meant to demonstrate.
# The capability axis is the full `{None, eval, evolve, publish, browse}` --
# every one of `CAPABILITIES` plus the bare-doctor sentinel.


def _ok_results() -> list[CheckResult]:
    return [
        CheckResult(id=id_, status="ok", severity=severity, detail="d", fix=None,
                    capabilities=caps)
        for id_, (severity, caps) in CHECK_SPECS.items()
    ]


def _flip(results: list[CheckResult], **status_by_id: str) -> list[CheckResult]:
    """A copy of ``results`` with each named check's ``status`` replaced --
    severity/capabilities/detail/fix untouched, so every override still
    carries CHECK_SPECS-accurate metadata."""
    return [
        CheckResult(id=r.id, status=status_by_id.get(r.id, r.status), severity=r.severity,
                    detail=r.detail, fix=r.fix, capabilities=r.capabilities)
        for r in results
    ]


# {scenario: (results, {capability: expected exit_code})}, reasoned directly
# from CHECK_SPECS (S5.6/INV6's fold rule), not from calling capability_ready
# itself -- eval/evolve have hard checks only (container_runtime/arena_image,
# plus mutator_image/operator for evolve) so soft status never touches them;
# publish/browse have NO hard checks of their own, so they fall back to their
# own soft checks (hub/hub_login/gh for publish, hub alone for browse), where
# only "fail" gates -- "warn" never does, for any capability.
_SCENARIOS: dict[str, tuple[list[CheckResult], dict[str | None, int]]] = {
    "all_ok": (
        _ok_results(),
        {None: 0, "eval": 0, "evolve": 0, "publish": 0, "browse": 0},
    ),
    "one_hard_fail": (
        # container_runtime tags BOTH eval and evolve at once -- the widest
        # single-check blast radius among the 4 hard checks.
        _flip(_ok_results(), container_runtime="fail"),
        {None: 1, "eval": 1, "evolve": 1, "publish": 0, "browse": 0},
    ),
    "one_soft_warn": (
        # All three soft checks warn at once -- still fully inert: a "warn"
        # never gates anything, for any capability (INV6).
        _flip(_ok_results(), hub="warn", hub_login="warn", gh="warn"),
        {None: 0, "eval": 0, "evolve": 0, "publish": 0, "browse": 0},
    ),
    "one_soft_fail": (
        # gh alone fails. gh tags ONLY publish (not browse) -- this is the
        # scenario that actually distinguishes publish from browse: publish
        # is gated (one of ITS soft checks failed) while browse is untouched
        # (its only tagged check, hub, is still ok).
        _flip(_ok_results(), gh="fail"),
        {None: 0, "eval": 0, "evolve": 0, "publish": 1, "browse": 0},
    ),
    "hard_fail_and_soft_warn": (
        # Combines the two independent behaviors above in one CheckResult
        # list: the hard fail still gates eval/evolve/None, and the soft
        # warns alongside it still gate nothing -- same shape as
        # "one_hard_fail" alone, confirming the warns contribute nothing.
        _flip(_ok_results(), container_runtime="fail", hub="warn", hub_login="warn", gh="warn"),
        {None: 1, "eval": 1, "evolve": 1, "publish": 0, "browse": 0},
    ),
}


@pytest.mark.parametrize("cap", [None, "eval", "evolve", "publish", "browse"])
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_fold_exhaustive_status_x_capability(scenario, cap):
    results, expected_by_cap = _SCENARIOS[scenario]
    assert exit_code(results, cap) == expected_by_cap[cap]
    # exit_code and capability_ready must never disagree (None resolves to
    # "eval" -- the same bare-doctor default exit_code itself applies).
    ready = capability_ready(results, cap if cap is not None else "eval")
    assert ready == (expected_by_cap[cap] == 0)


def test_fold_probe_crash_matches_an_authored_hard_fail():
    # The 5th scenario the brief names -- "probe-crash -> fail" -- has no
    # hand-buildable CheckResult shape: there's no "crashed" status, only
    # "ok"/"warn"/"fail". `_safe` (diagnostics.py) turns ANY raising probe
    # into an ordinary status="fail" CheckResult, preserving the crashed
    # check's severity/capabilities straight from CHECK_SPECS. So this drives
    # the SAME docker_available crash as
    # test_a_raising_probe_becomes_a_failed_check_not_an_exception (above)
    # through the real run_checks -> exit_code pipeline, across every
    # capability -- not just asserting the crashed CheckResult's own fields
    # (already covered there), but that it gates EXACTLY like the hand-built
    # "one_hard_fail" scenario above, for every capability.
    def _boom():
        raise RuntimeError("docker vanished")

    results = run_checks(**_healthy_kwargs(runtime_report=_boom))
    _, expected_by_cap = _SCENARIOS["one_hard_fail"]
    for cap, expected in expected_by_cap.items():
        assert exit_code(results, cap) == expected, cap
