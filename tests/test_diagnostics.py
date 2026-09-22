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
from nethackers.harness import sandbox_preflight
from nethackers.hubclient.client import HubUnreachable
from nethackers.hubclient.credentials import Credentials
from nethackers.setup.host import HostFacts

_HEX64 = "a" * 64

# A Mac with Homebrew and a running Docker Desktop: the realistic default for
# these tests, and a platform setup covers (so fix hints point at setup).
_MAC = HostFacts(system="Darwin", machine="arm64", brew=True,
                 installed=frozenset({"docker", "docker-desktop"}),
                 docker_context="desktop-linux", docker_desktop_cli=True,
                 host_rosetta=True, cpus=10, memory_gb=32)
_WINDOWS = HostFacts(system="Windows", machine="AMD64")
_UBUNTU = HostFacts(system="Linux", machine="x86_64", distro="debian")


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
        opencode2_keyed=lambda: True,   # never read the real ~/.config/opencode
        hub_mode=lambda hub: "github",
        load_creds=lambda: Credentials("castiel", "tok"),
        gh_state=lambda: ("castiel", "authed"),
        # Without this override, the real default reads the actual host's
        # Docker Desktop settings file -- a real, uncontrolled probe that
        # would break this file's "no real...call" hermeticity guarantee.
        # Harmless to gating either way (the check is soft, and eval/evolve
        # are gated purely by their own hard checks) -- but this file's whole
        # premise is that every probe is faked.
        rosetta=lambda: ("ok", "Rosetta is accelerating amd64 emulation", None),
        host_facts=lambda: _MAC,
    )
    kwargs.update(overrides)
    return kwargs


def test_run_checks_returns_one_result_per_check_id():
    results = run_checks(**_healthy_kwargs())
    ids = {r.id for r in results}
    assert ids == {
        "container_runtime", "arena_image", "mutator_image",
        "hub", "hub_login", "gh", "operator", "rosetta",
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


def test_only_none_default_is_unchanged_and_runs_all_eight():
    # Backward compatibility is the whole point of `only`: every existing
    # caller (the `doctor` CLI, this file's own healthy-path tests above)
    # omits it, and must see byte-for-byte the same 8-check behavior as
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
        only={"operator"}, preflight_operator=lambda operator: "not logged in",
        host_facts=lambda: _WINDOWS))
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


def test_check_specs_covers_exactly_the_eight_check_ids():
    assert set(CHECK_SPECS) == {
        "container_runtime", "arena_image", "mutator_image",
        "hub", "hub_login", "gh", "operator", "rosetta",
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
    assert op2.status == "fail" and op2.fix == "run `nethackers setup`"


def test_operator_narrows_to_a_single_named_agent():
    seen = []
    results = run_checks(**_healthy_kwargs(
        operator="codex", preflight_operator=lambda op: seen.append(op) or None))
    op = next(r for r in results if r.id == "operator")
    assert seen == ["codex"]                           # ONLY codex probed
    assert "codex" in op.detail and "claude" not in op.detail


def test_operator_labels_keyless_opencode2_as_free_models_only():
    # opencode2 always passes (its free models need no key), but calling that
    # "logged in" claimed a login that doesn't exist.
    def check(keyed: bool) -> tuple[str, str]:
        results = run_checks(**_healthy_kwargs(
            operator="opencode2", opencode2_keyed=lambda: keyed))
        op = next(r for r in results if r.id == "operator")
        return op.status, op.items[0].detail

    assert check(False) == ("ok", "free models only")
    assert check(True) == ("ok", "logged in")


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


# --- image "unreachable" fix text: per-KIND, because the two kinds differ


def test_image_unreachable_fix_suggests_make_inside_a_repo_checkout():
    # Arena's real resolve_image never returns a digest inside a checkout --
    # its local dev tag (sandbox_preflight.py's `nethackers/arena:dev`) --
    # unlike the shared `_ref` fake (a digest shape, by design: see its own
    # comment). Fix round 1 makes the fallback's fix-text selection care
    # about that shape, so this fixture must reflect the real non-digest ref
    # to exercise the checkout/`make` path rather than the digest/network one.
    results = run_checks(**_healthy_kwargs(
        resolve_image=lambda explicit, kind: "nethackers/arena:dev",
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=lambda: Path("/fake/repo"),
    ))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "fail"
    assert "make mutator" in mutator.fix
    assert "NETHACKERS_" not in mutator.fix


def test_image_unreachable_fix_never_suggests_make_for_a_digest_ref():
    # A checkout whose mutator matches its pin resolves to the pinned digest
    # (Task 5's resolve_image), and nethackers only ever pulls a digest
    # (ensure_image) -- never builds it, even inside a checkout. `make
    # mutator` can't fix an unreachable digest, so the fix must still name
    # the network, exactly like outside a repo.
    results = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=lambda: Path("/fake/repo"),
    ))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "fail"
    assert "make" not in mutator.fix
    assert "NETHACKERS_MUTATOR_IMAGE" in mutator.fix


def test_image_unreachable_fix_suggests_network_override_outside_a_repo():
    results = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=lambda: None,
    ))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "fail"
    assert "make" not in mutator.fix
    assert "NETHACKERS_MUTATOR_IMAGE" in mutator.fix


@pytest.mark.parametrize("repo_root", [lambda: Path("/fake/repo"), lambda: None])
def test_arena_unreachable_fix_never_suggests_a_build_even_in_a_checkout(repo_root):
    """The arena resolves to the pinned digest everywhere (spec D6) and
    ``ensure_image`` pulls a digest ref rather than building it (INV11), so
    `make arena` would build a tag this run is not going to use. doctor must
    not print advice its own acquisition path will not follow -- which is also
    what docs/troubleshooting.md's "unreachable" entry tells the reader."""
    results = run_checks(**_healthy_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: False,
        repo_root=repo_root,
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


def test_gh_states_get_distinct_details_and_setup_fixes():
    missing = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "missing")))
    unauthed = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "unauthed")))
    authed = run_checks(**_healthy_kwargs(gh_state=lambda: ("castiel", "authed")))
    gh_missing = next(r for r in missing if r.id == "gh")
    gh_unauthed = next(r for r in unauthed if r.id == "gh")
    gh_authed = next(r for r in authed if r.id == "gh")

    # never collapse "not installed" and "installed but not logged in" (spec
    # S5.6): the details stay distinct even though setup fixes both on a Mac.
    assert gh_missing.detail == "gh is not installed"
    assert gh_unauthed.detail == "gh is installed but not logged in"
    assert gh_missing.fix == gh_unauthed.fix == "run `nethackers setup`"
    assert gh_authed.status == "ok" and gh_authed.fix is None


def test_gh_missing_on_linux_prints_the_distro_install_line():
    results = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "missing"),
                                           host_facts=lambda: _UBUNTU))
    gh = next(r for r in results if r.id == "gh")
    assert gh.fix == ("install the GitHub CLI: `sudo apt install gh` (or see "
                      "https://github.com/cli/cli/blob/trunk/docs/install_linux.md); then run `nethackers setup`")


def test_without_setup_support_gh_fixes_keep_the_direct_commands():
    missing = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "missing"),
                                           host_facts=lambda: _WINDOWS))
    unauthed = run_checks(**_healthy_kwargs(gh_state=lambda: (None, "unauthed"),
                                            host_facts=lambda: _WINDOWS))
    assert "install" in next(r for r in missing if r.id == "gh").fix.lower()
    assert "gh auth login" in next(r for r in unauthed if r.id == "gh").fix


def test_operator_check_uses_the_given_operator_name():
    seen = []
    results = run_checks(**_healthy_kwargs(
        operator="codex",
        preflight_operator=lambda operator: seen.append(operator) or "nope",
    ))
    assert seen == ["codex"]
    op = next(r for r in results if r.id == "operator")
    assert op.status == "fail" and op.severity == "hard" and "codex" in op.fix


def test_operator_fix_names_setup_with_the_agent():
    results = run_checks(**_healthy_kwargs(operator="claude",
                                           preflight_operator=lambda op: "not logged in"))
    op = next(r for r in results if r.id == "operator")
    assert op.fix == "run `nethackers setup --operator claude`"


def test_operator_fallback_uses_the_real_claude_login_command():
    results = run_checks(**_healthy_kwargs(operator="claude", host_facts=lambda: _WINDOWS,
                                           preflight_operator=lambda op: "not logged in"))
    op = next(r for r in results if r.id == "operator")
    assert "`claude auth login`" in op.fix and "`claude login`" not in op.fix


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
    assert hl.status == "fail" and hl.fix == "run `nethackers setup`"
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
# (publish/browse, in the real 8-check table) falls back to its own soft
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


# --- a checkout's mutator fingerprint ref (spec 2026-09-15 §5.6) ------------

_FP = "nethackers/mutator:h-" + "e" * 64
_FP_REMOTE = "ghcr.io/dunnolab/nethackers-mutator:h-" + "e" * 64


def _fingerprint_resolver(explicit, kind):
    return _FP if kind == "mutator" else _ref(explicit, kind)


def test_fingerprint_mutator_present_is_ok():
    results = run_checks(**_healthy_kwargs(resolve_image=_fingerprint_resolver))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "ok" and _FP in mutator.detail


def test_fingerprint_mutator_published_by_ci_is_pullable():
    probed = []
    results = run_checks(**_healthy_kwargs(
        resolve_image=_fingerprint_resolver,
        image_present=lambda ref: ref != _FP,
        manifest_reachable=lambda ref: probed.append(ref) or ref == _FP_REMOTE,
    ))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "warn" and "pullable" in mutator.detail
    assert _FP_REMOTE in probed                          # asks GHCR under its published name


def test_fingerprint_mutator_nobody_built_says_it_builds_on_first_use():
    results = run_checks(**_healthy_kwargs(
        resolve_image=_fingerprint_resolver,
        image_present=lambda ref: ref != _FP,
        manifest_reachable=lambda ref: False,
    ))
    mutator = next(r for r in results if r.id == "mutator_image")
    assert mutator.status == "warn"
    assert mutator.fix == "run `nethackers setup`"


def test_short_digest_also_shortens_fingerprint_tags():
    shortened = _short_digest(f"present — {_FP}")
    assert shortened == "present — nethackers/mutator:h-" + "e" * 19 + "…"


# --- the registry probe: doctor must not be stricter than acquisition -------


def test_doctor_probe_is_not_stricter_than_the_acquisition_probe():
    # A slow registry made doctor report `unreachable` for an image ensure_image
    # pulls without complaint: `docker manifest inspect` walks every sub-manifest
    # of a multi-arch index, which measured 7-16s against GHCR on a laptop --
    # over doctor's old 10s budget, inside acquisition's. Both probes now share
    # one budget, so the two can't disagree about the same image.
    seen: dict = {}

    def _run(argv, **kw):
        seen[argv[1]] = kw.get("timeout")
        return SimpleNamespace(returncode=0)

    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "a" * 64
    assert diagnostics._manifest_reachable(ref, run=_run) is True
    assert sandbox_preflight._remote_image_exists(ref, runtime="docker", run=_run) is True
    assert seen["manifest"] == sandbox_preflight.MANIFEST_PROBE_TIMEOUT >= 30


# --- container runtime fixes come from the OS files -------------------------


def _runtime_fix(facts: HostFacts, report) -> str | None:
    results = run_checks(**_healthy_kwargs(runtime_report=lambda: report,
                                           host_facts=lambda: facts))
    return next(r for r in results if r.id == "container_runtime").fix


def test_container_runtime_fix_on_a_mac_with_homebrew_is_setup():
    fresh = HostFacts(system="Darwin", machine="arm64", brew=True)
    assert _runtime_fix(fresh, _runtime_none()) == "run `nethackers setup`"


def test_container_runtime_fix_on_linux_prints_the_install_then_setup():
    fix = _runtime_fix(_UBUNTU, _runtime_none())
    assert fix is not None
    assert fix.startswith("install Docker: `curl -fsSL https://get.docker.com | sudo sh`")
    assert fix.endswith("then run `nethackers setup`")


def test_container_runtime_fix_on_native_windows_says_not_covered():
    from nethackers.setup.host import NOT_COVERED
    assert _runtime_fix(_WINDOWS, _runtime_none()) == NOT_COVERED


def test_host_facts_are_probed_lazily_and_at_most_once():
    calls: list[int] = []

    def facts() -> HostFacts:
        calls.append(1)
        return _MAC

    run_checks(**_healthy_kwargs(host_facts=facts))          # all healthy: nothing needs them
    assert calls == []
    run_checks(**_healthy_kwargs(host_facts=facts, load_creds=lambda: None,
                                 gh_state=lambda: (None, "unauthed")))
    assert calls == [1]                                       # two fixes, one probe
