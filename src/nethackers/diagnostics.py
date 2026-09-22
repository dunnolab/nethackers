"""Diagnostics: the version regimes ``nethackers`` reports about itself, and
per-capability machine readiness.

``version_info()`` backs the top-level ``--version`` flag (cli.py) and is
also the single builder ``doctor -o json``'s ``env`` header extends --
INV5's "one source of truth" for what a build IS, as opposed to what's
actually installed/reachable on this machine (``run_checks``/``doctor``'s
job, below) or what actually ran on a given evolve run (the per-run
provenance record, ``harness/runlog.py``).

``run_checks``/``capability_ready``/``exit_code``/``to_json``/
``render_human``/``render_plain`` back ``nethackers doctor``: three pure
stages -- probe (``run_checks``, every dependency injectable, NEVER raises),
fold (``capability_ready``/``exit_code``, a pure function of the results),
present (``to_json``/``render_human``/``render_plain``) -- kept separate so
the fold itself is exhaustively table-testable independent of any probe.
``hub``/``hub_login`` reuse the exact primitives ``cli.py``'s ``whoami``
handler calls (``HubClient(hub).hub_mode()`` / ``hubclient.credentials.
load()``) rather than duplicating that logic -- one source of truth (INV5)
for "am I logged in" / "is the hub reachable", without this module ever
importing ``cli`` itself (which imports FROM here -- that would cycle)."""
from __future__ import annotations

import platform
import re
import subprocess
from collections.abc import Callable, Collection
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version
from pathlib import Path

from nethackers import _image_pins
from nethackers.config import load_stage
from nethackers.containers import RuntimeReport, container_runtime, probe_container_runtime
from nethackers.harness import sandbox_preflight
from nethackers.harness.auth_inject import opencode2_has_provider_key
from nethackers.harness.version import RUN_SCHEMA_VERSION
from nethackers.hubclient.client import HubClient, HubUnreachable
from nethackers.hubclient.credentials import Credentials, load as _default_load_creds
from nethackers.hubclient.publish import gh_state as _default_gh_state
from nethackers.operators import OPERATORS
from nethackers.setup import host as _host
from nethackers.setup.host import HostFacts
from nethackers.setup.support import SETUP_CMD, fix_text


def version_info() -> dict:
    """The three version regimes this build ships as: the **package**
    (``importlib.metadata`` -- the same string ``pip show nethackers``
    reports), the **run/publish format** (``RUN_SCHEMA_VERSION``, unrelated
    to the package version -- see ``harness/version.py``), and the **pinned
    sandbox images** this build was cut against (``_image_pins`` -- what a
    fresh install pulls, not necessarily what's locally present already)."""
    return {
        "nethackers": _pkg_version("nethackers"),
        "run_schema_version": RUN_SCHEMA_VERSION,
        "images": {"arena": _image_pins.ARENA_IMAGE, "mutator": _image_pins.MUTATOR_IMAGE},
    }


# ============================================================================
# `nethackers doctor` -- per-capability readiness (spec S5.6)
# ============================================================================

CAPABILITIES = ("eval", "evolve", "publish", "browse")

# The single source for each check's (severity, capabilities) -- id -> (sev,
# caps). `run_checks` reads this per id and passes both values to BOTH the
# `_safe(...)` crash-path wrapper and the corresponding `_check_*` builder,
# so neither can drift out of sync with the other (previously each was a
# separately hand-written literal at two call sites). Also the constant a
# future exhaustive fold-table test reads, rather than re-deriving the same
# 8-row table a third time.
CHECK_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "container_runtime": ("hard", ("eval", "evolve")),
    "arena_image": ("hard", ("eval", "evolve")),
    "mutator_image": ("hard", ("evolve",)),
    "hub": ("soft", ("browse", "publish")),
    "hub_login": ("soft", ("publish",)),
    "gh": ("soft", ("publish",)),
    "operator": ("hard", ("evolve",)),
    # Soft severity, tagged to eval/evolve -- exactly where amd64 emulation
    # cost is actually paid (an earlier version tagged this with an empty
    # capability tuple; that made it un-gating but also unrenderable, since
    # render_human/render_plain group rows by `cap in r.capabilities` --
    # capability-less rows can never appear there). Spec I9 (never moves
    # doctor's exit code) now rests on two things instead: setup.macos.emulation/
    # _check_rosetta never emit "fail" (only "ok"/"warn" -- see
    # tests/test_doctor_rosetta.py::test_check_rosetta_never_emits_fail), and
    # capability_ready's own fold -- eval/evolve both carry hard checks of
    # their own, so they're gated PURELY by those; a soft check tagged onto
    # a capability that has hard checks (this one, on both) never enters
    # into the verdict at all, "ok" or "warn" alike.
    "rosetta": ("soft", ("eval", "evolve")),
}


@dataclass(frozen=True)
class CheckItem:
    """One sub-entry of a check that has a per-item breakdown -- e.g. each
    registered coding agent under the ``operator`` check. Rendered as an
    indented sublist beneath the parent check; the same data also stays
    flattened into the parent ``CheckResult.detail``, so ``-o json`` (and its
    committed schema) need no new field."""

    label: str    # e.g. "codex"
    status: str   # "ok" | "warn" | "fail"
    detail: str   # e.g. "logged in"


@dataclass(frozen=True)
class CheckResult:
    """One named check's outcome.

    ``status`` is the check's own finding (``"ok"``/``"warn"``/``"fail"``).
    ``severity`` says how it participates in ``capability_ready`` (see
    there for the exact fold): ``"hard"`` means a capability tagged with
    this check is gated purely by its hard checks (a non-``"ok"`` status
    here always makes every one of ``capabilities`` unready); ``"soft"``
    means this check only gates a capability that has NO hard checks of
    its own (only ``"fail"`` gates there -- ``"warn"`` never gates
    anything, for any capability -- INV6).

    ``fix`` is a single, ready-to-run next step, or ``None`` when
    ``status == "ok"``. ``capabilities`` names every capability (from
    ``CAPABILITIES``) this check's outcome is relevant to -- a check may
    (and several do) belong to more than one.
    """

    id: str
    status: str  # "ok" | "warn" | "fail"
    severity: str  # "hard" | "soft"
    detail: str
    fix: str | None
    capabilities: tuple[str, ...]
    # Optional per-item breakdown rendered as an indented sublist (e.g. one
    # line per registered agent under `operator`); `()` for the checks that
    # are a single line. NOT emitted by `to_json` -- `detail` carries the same
    # data flattened, so the committed doctor.schema.json is unaffected.
    items: tuple[CheckItem, ...] = ()


def _manifest_reachable(ref: str, *, runtime: str = "docker", run=subprocess.run) -> bool:
    """Cheap existence probe for an image ref that isn't present locally yet:
    ``<runtime> manifest inspect`` hits the registry's manifest endpoint
    without pulling any layers, so a not-yet-pulled-but-publishable ref
    ("pullable") can be told apart from one the registry doesn't have / a
    registry that can't be reached at all ("unreachable") -- spec S5.6.
    ``False`` on ANY failure (404, stale auth, network down, runtime itself
    missing): distinguishing *why* isn't doctor's job here -- ``ensure_
    image``'s own error mapping already covers that at actual-pull time.
    ``runtime`` is threaded in from ``run_checks`` (``container_runtime()``) so
    a podman-only host probes with podman, not a missing ``docker``. The budget
    is ``sandbox_preflight``'s, so a registry slow enough to outlast it can't
    make doctor report "unreachable" for an image ``ensure_image`` then pulls."""
    try:
        result = run([runtime, "manifest", "inspect", ref], capture_output=True,
                     timeout=sandbox_preflight.MANIFEST_PROBE_TIMEOUT)
        return bool(result.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return False


def _default_hub_mode(hub: str) -> str | None:
    """The exact call ``whoami`` makes (``cli.py``'s handler) --
    ``HubClient(hub).hub_mode()`` -- so the ``hub`` check shares one source
    of truth with it (INV5). May raise ``HubUnreachable``; ``_check_hub`` is
    the only place that catches it."""
    return HubClient(hub).hub_mode()


def _default_opencode2_keyed() -> bool:
    """Whether a global OpenCode provider carries a key the sandbox can use
    (``auth_inject``, the same rule a run applies)."""
    return opencode2_has_provider_key(home=Path.home())


_RUNTIME_ITEM_STATUS = {"usable": "ok", "absent": "warn", "broken": "fail"}

# The real login command per coding agent, for the fallback fix text (where
# setup doesn't cover the OS). OpenCode has none: it runs without a login.
_AGENT_LOGIN = {"claude": "claude auth login", "codex": "codex login"}


def _check_container_runtime(
    *, severity: str, caps: tuple[str, ...], runtime_report: Callable[[], RuntimeReport],
    runtime_fix: Callable[[RuntimeReport], str | None],
) -> CheckResult:
    """OK as soon as ONE runtime (docker/podman) is usable; otherwise a per-CLI
    breakdown so the user sees WHY -- ``docker`` present but ``info`` failed
    (daemon down, socket permission denied, rootless unconfigured) vs. nothing
    installed at all (issue #50). The fix comes from this OS's setup recipes
    (``runtime_fix``): "run `nethackers setup`" where setup can bring a runtime
    up itself, otherwise the exact commands to run."""
    report = runtime_report()
    if report.runtime is not None:
        return CheckResult(id="container_runtime", status="ok", severity=severity,
                           detail=f"{report.runtime} is available", fix=None,
                           capabilities=caps)
    items = tuple(
        CheckItem(
            label=c.exe,
            status=_RUNTIME_ITEM_STATUS[c.state],
            detail=("available" if c.state == "usable"
                    else "not installed" if c.state == "absent"
                    else c.detail),
        )
        for c in report.candidates
    )
    broken = [c for c in report.candidates if c.state == "broken"]
    detail = ("; ".join(f"{c.exe}: {c.detail}" for c in broken) if broken  # flattened for -o json
              else "no docker or podman found on PATH")
    return CheckResult(id="container_runtime", status="fail", severity=severity,
                       detail=detail, fix=runtime_fix(report), capabilities=caps, items=items)


def _check_image(
    kind: str,
    *,
    severity: str,
    caps: tuple[str, ...],
    resolve_image: Callable[[str | None, str], str],
    image_present: Callable[[str], bool],
    manifest_reachable: Callable[[str], bool],
    repo_root: Callable[[], Path | None],
    setup_hint: Callable[[], str | None],
) -> CheckResult:
    """``present`` (already local) / ``warn``-``pullable`` (not local, but
    the registry has it -- ``nethackers doctor --pull`` fetches it) /
    ``fail``-``unreachable`` (neither) -- spec S5.6. The "unreachable" fix
    text mirrors ``ensure_image``'s own real branch order (spec 2026-09-15
    §5.5), keyed off the ref's shape, not just the checkout: a digest ref
    (``"@sha256:" in ref``) is only ever pulled, checkout or not, so its fix
    always names the network; a checkout's fingerprint mutator ref is
    pulled-or-built (the branch above, before this one ever runs); any other
    ref inside a checkout (the arena's local dev tag) is built locally, so
    its fix names ``make``; outside a checkout the only path left, for
    anything else, is the network/registry."""
    check_id = f"{kind}_image"
    ref = resolve_image(None, kind)
    if image_present(ref):
        return CheckResult(id=check_id, status="ok", severity=severity,
                           detail=f"present — {ref}", fix=None, capabilities=caps)
    if sandbox_preflight.is_local_mutator_fingerprint(ref):
        # A checkout whose mutator files differ from the pinned build. CI may have
        # published an image for exactly these files; otherwise nethackers builds it.
        remote = sandbox_preflight.ghcr_mutator_ref(ref)
        if manifest_reachable(remote):
            return CheckResult(id=check_id, status="warn", severity=severity,
                               detail=f"not local yet, but pullable — {remote}",
                               fix=setup_hint() or "run `nethackers doctor --pull` to fetch it now",
                               capabilities=caps)
        return CheckResult(id=check_id, status="warn", severity=severity,
                           detail=(f"not built yet — {ref} (this checkout's mutator files "
                                   "differ from the pinned build)"),
                           fix=setup_hint() or ("run `nethackers doctor --pull` to build it now, "
                                                "or just start evolve, which builds it"),
                           capabilities=caps)
    if manifest_reachable(ref):
        return CheckResult(id=check_id, status="warn", severity=severity,
                           detail=f"not local yet, but pullable — {ref}",
                           fix=setup_hint() or "run `nethackers doctor --pull` to fetch it now",
                           capabilities=caps)
    if repo_root() is not None and "@sha256:" not in ref:
        fix = f"run `make {kind}` (or just `nethackers eval`/`evolve`, which auto-builds it)"
    else:
        fix = (f"check your network connection, or set NETHACKERS_{kind.upper()}_IMAGE "
              "to a reachable ref")
    return CheckResult(id=check_id, status="fail", severity=severity,
                       detail=f"unreachable — {ref}", fix=fix, capabilities=caps)


def _check_hub(
    hub: str, *, severity: str, caps: tuple[str, ...], hub_mode: Callable[[str], str | None],
) -> CheckResult:
    try:
        mode = hub_mode(hub)
    except HubUnreachable:
        return CheckResult(id="hub", status="fail", severity=severity,
                           detail=f"hub unreachable at {hub}",
                           fix=f"check --hub {hub} is correct, or your network connection",
                           capabilities=caps)
    detail = "reachable" if mode is None else f"reachable — auth={mode}"
    return CheckResult(id="hub", status="ok", severity=severity, detail=detail, fix=None,
                       capabilities=caps)


def _check_hub_login(
    *, severity: str, caps: tuple[str, ...], load_creds: Callable[[], Credentials | None],
    setup_hint: Callable[[], str | None],
) -> CheckResult:
    creds = load_creds()
    if creds is not None:
        return CheckResult(id="hub_login", status="ok", severity=severity,
                           detail=f"logged in as @{creds.login}", fix=None, capabilities=caps)
    return CheckResult(id="hub_login", status="fail", severity=severity, detail="not logged in",
                       fix=setup_hint() or "run `nethackers login`", capabilities=caps)


def _check_gh(
    *, severity: str, caps: tuple[str, ...], gh_state: Callable[[], tuple[str | None, str]],
    setup_hint: Callable[[], str | None], install_fix: Callable[[], str | None],
) -> CheckResult:
    login, state = gh_state()
    if state == "authed":
        return CheckResult(id="gh", status="ok", severity=severity, detail=f"authed as @{login}",
                           fix=None, capabilities=caps)
    if state == "unauthed":
        return CheckResult(id="gh", status="fail", severity=severity,
                           detail="gh is installed but not logged in",
                           fix=(setup_hint()
                               or "run `gh auth login` (separate from `nethackers login`)"),
                           capabilities=caps)
    return CheckResult(id="gh", status="fail", severity=severity, detail="gh is not installed",
                       fix=(install_fix()
                           or "install the GitHub CLI (`gh`), then run `gh auth login`"),
                       capabilities=caps)


def _check_operator(
    operators: tuple[str, ...], *, severity: str, caps: tuple[str, ...],
    mutator_present: bool, preflight_operator: Callable[[str], str | None],
    opencode2_keyed: Callable[[], bool], setup_hint: Callable[[], str | None],
) -> CheckResult:
    """Host-login readiness across the registered coding agents. Probes EVERY
    agent the caller asked about (``run_checks(operator=None)`` -> all of
    ``operators.OPERATORS``; a single name -> just that one) rather than one
    agent plus a "note the other": evolve drives ONE operator chosen at Start,
    so this is ready as long as AT LEAST ONE agent is logged in, and the detail
    lists each agent's status so the options are visible.

    OpenCode 2 is always usable (free models need no key), so it always
    counts as ready -- but without a provider key it says "free models only"
    rather than claiming a login."""
    status = {op: preflight_operator(op) for op in operators}  # None == logged in

    def ready_detail(op: str) -> str:
        if op == "opencode2" and not opencode2_keyed():
            return "free models only"
        return "logged in"

    items = tuple(
        CheckItem(label=op, status="ok" if status[op] is None else "fail",
                  detail=ready_detail(op) if status[op] is None else "not logged in")
        for op in operators)
    flat = ", ".join(f"{it.label}: {it.detail}" for it in items)  # flattened for -o json
    if any(status[op] is None for op in operators):
        return CheckResult(id="operator", status="ok", severity=severity, detail=flat,
                           fix=None, capabilities=caps, items=items)
    hint = setup_hint()
    if hint is not None:
        # setup installs the agent if needed and runs its own login.
        fix = (f"run `{SETUP_CMD} --operator {operators[0]}`" if len(operators) == 1 else hint)
    else:
        logins = " or ".join(f"`{_AGENT_LOGIN[op]}`" for op in operators if op in _AGENT_LOGIN)
        fix = f"log in to a coding agent — {logins}"
        if not mutator_present:
            # The mutator image is what actually runs the operator, but doctor never
            # force-pulls it just to double-check a host login (read-only outside
            # `--pull`) -- name that option rather than trust an unverifiable
            # host-only signal.
            fix += " — or `nethackers doctor --pull` to pull the sandbox and verify inside it"
    return CheckResult(id="operator", status="fail", severity=severity, detail=flat,
                       fix=fix, capabilities=caps, items=items)


def _check_rosetta(
    *,
    severity: str,
    caps: tuple[str, ...],
    rosetta: Callable[[], tuple[str, str, str | None]],
) -> CheckResult:
    """The amd64-emulation advisory (``setup.macos.emulation`` per runtime).
    ``rosetta`` returns ``(state, detail, fix)``; ``state`` is "ok", "warn" or
    "unknown". ``CheckResult.status`` admits only ok/warn/fail, and "unknown"
    must not read as a problem, so it surfaces as "ok" with the nuance in the
    detail. Never "fail" (spec I9)."""
    state, detail, fix = rosetta()
    status = "warn" if state == "warn" else "ok"
    return CheckResult(id="rosetta", status=status, severity=severity, detail=detail,
                       fix=fix if status == "warn" else None, capabilities=caps)


def _safe(
    check_id: str, severity: str, capabilities: tuple[str, ...], build: Callable[[], CheckResult],
) -> CheckResult:
    """Run one check's builder, turning ANY exception into a failed
    ``CheckResult`` for that check id instead of propagating -- ``doctor``
    must never traceback just because one probe (a flaky ``docker`` call,
    an unexpected fake in a test) blew up. Each check gets its own ``_safe``
    call, so one crashing probe never skips or corrupts the others."""
    try:
        return build()
    except Exception as exc:
        return CheckResult(id=check_id, status="fail", severity=severity,
                           detail=f"check crashed: {type(exc).__name__}: {exc}", fix=None,
                           capabilities=capabilities)


def run_checks(
    *,
    operator: str | None = None,
    hub: str | None = None,
    runtime_report: Callable[[], RuntimeReport] = probe_container_runtime,
    resolve_image: Callable[[str | None, str], str] = sandbox_preflight.resolve_image,
    image_present: Callable[[str], bool] | None = None,
    manifest_reachable: Callable[[str], bool] | None = None,
    repo_root: Callable[[], Path | None] = sandbox_preflight._repo_root,
    preflight_operator: Callable[[str], str | None] = sandbox_preflight.preflight_operator,
    opencode2_keyed: Callable[[], bool] = _default_opencode2_keyed,
    hub_mode: Callable[[str], str | None] = _default_hub_mode,
    load_creds: Callable[[], Credentials | None] = _default_load_creds,
    gh_state: Callable[[], tuple[str | None, str]] = _default_gh_state,
    rosetta: Callable[[], tuple[str, str, str | None]] | None = None,
    host_facts: Callable[[], HostFacts] | None = None,
    only: Collection[str] | None = None,
) -> list[CheckResult]:
    """The 8 checks behind ``nethackers doctor`` (spec S5.6). NEVER raises,
    regardless of what any injected probe does (each check runs under its
    own ``_safe``). Every dependency is injectable with a real, working
    default, so a bare call is a genuine (if possibly slow/networked)
    doctor run, and a test call substitutes fakes for every one of them --
    no real docker/network call is made by this function's own test suite.

    ``hub=None`` (the default) resolves the EFFECTIVE stage's hub
    (``load_stage().hub_url`` -- the same late-bound pattern ``launch.py``'s
    ``EvolveParams`` default factories use) rather than freezing the bare
    prod URL at import time -- a future bare call (the TUI evolve panel,
    INV5) must see a worktree's ``.env.stack``/env-configured hub, not
    always prod. ``cli.py``'s handler still always passes ``hub=args.hub``
    explicitly (already resolved through the full flag/env/file ladder), so
    this only matters for a caller that omits ``hub=`` entirely.

    ``only``, when given, restricts execution to just the named check ids
    (keys of ``CHECK_SPECS``) -- every other check is skipped ENTIRELY: its
    probe is never called, not merely hidden from the returned list. ``None``
    (the default) runs all 8, byte-for-byte unchanged for every existing
    caller (the ``doctor`` CLI, the schema/conformance tests). This exists
    for a targeted, DISPLAY-ONLY caller that only ever shows a subset of the
    8 checks and must not trigger the others' network/subprocess calls just
    to throw the results away -- e.g. the TUI's evolve-readiness strip, which
    must never make a hub HTTPS round-trip / ``gh`` subprocess / creds-file
    read just because the user opened that tab (spec S5.8). A filtered
    result must NEVER be passed to ``to_json``: its ``capabilities`` map
    assumes all 8 checks ran, so an un-run capability's tagged checks would
    simply be absent from ``results`` and ``capability_ready`` would read it
    as vacuously ready (INV6's fold has nothing to gate on).

    ``host_facts`` (default: ``setup.host.detect_host``) is what the fix hints
    and the Rosetta check read. It is probed at most once per call, and only
    when something needs it: a failing check's fix, or the default Rosetta
    probe. A caller that injects ``rosetta`` and whose checks all pass never
    probes it; a caller that runs the Rosetta check with the default probe --
    the TUI's evolve strip does, off the UI thread -- pays for one probe: a
    few quick local commands (``docker context show`` and the like).
    """
    effective_hub = hub if hub is not None else load_stage().hub_url
    results: list[CheckResult] = []

    facts_seen: list[HostFacts] = []

    def _facts() -> HostFacts:
        if not facts_seen:
            facts_seen.append((host_facts or _host.detect_host)())
        return facts_seen[0]

    def _setup_hint() -> str | None:
        return f"run `{SETUP_CMD}`" if _host.setup_supported(_facts().system) else None

    def _runtime_fix(report: RuntimeReport) -> str | None:
        facts = _facts()
        plat = _host.platform_for(facts)
        return _host.NOT_COVERED if plat is None else fix_text(plat.runtime_recipes(facts, report))

    def _gh_install_fix() -> str | None:
        facts = _facts()
        plat = _host.platform_for(facts)
        return None if plat is None else fix_text((plat.gh_install_recipe(facts),))

    def _default_rosetta() -> tuple[str, str, str | None]:
        facts = _facts()
        plat = _host.platform_for(facts)
        if plat is None:
            return "unknown", "amd64 emulation isn't checked on this platform", None
        state, detail, recipe = plat.emulation(facts)
        return state, detail, (recipe.say if recipe is not None else None)

    rosetta_probe = rosetta or _default_rosetta

    # The two image probes need the SAME container runtime the rest of the
    # machine uses, so a podman-only host doesn't report a pullable image as
    # "unreachable" just because there's no `docker` binary (issue #50). Bind
    # the real defaults to a freshly-resolved runtime here, at call time (never
    # an import-time bound default -- see the injectable-seam trap); an injected
    # fake (every test) bypasses this branch entirely.
    if image_present is None or manifest_reachable is None:
        _rt = container_runtime() or "docker"

        def _probe_present(ref: str) -> bool:
            return sandbox_preflight.image_present(ref, runtime=_rt)

        def _probe_manifest(ref: str) -> bool:
            return _manifest_reachable(ref, runtime=_rt)

        image_present = image_present or _probe_present
        manifest_reachable = manifest_reachable or _probe_manifest

    def _wanted(check_id: str) -> bool:
        return only is None or check_id in only

    if _wanted("container_runtime"):
        severity, caps = CHECK_SPECS["container_runtime"]
        results.append(_safe("container_runtime", severity, caps,
                             lambda: _check_container_runtime(severity=severity, caps=caps,
                                                              runtime_report=runtime_report,
                                                              runtime_fix=_runtime_fix)))

    if _wanted("arena_image"):
        severity, caps = CHECK_SPECS["arena_image"]
        results.append(_safe("arena_image", severity, caps,
                             lambda: _check_image("arena", severity=severity, caps=caps,
                                                  resolve_image=resolve_image,
                                                  image_present=image_present,
                                                  manifest_reachable=manifest_reachable,
                                                  repo_root=repo_root,
                                                  setup_hint=_setup_hint)))

    # `mutator_present` feeds `operator`'s fix text below -- default to the
    # safe "absent" assumption when `mutator_image` itself was filtered out of
    # `only`, rather than referencing a result that was never computed.
    mutator_present = False
    if _wanted("mutator_image"):
        severity, caps = CHECK_SPECS["mutator_image"]
        mutator_result = _safe("mutator_image", severity, caps,
                               lambda: _check_image("mutator", severity=severity, caps=caps,
                                                    resolve_image=resolve_image,
                                                    image_present=image_present,
                                                    manifest_reachable=manifest_reachable,
                                                    repo_root=repo_root,
                                                    setup_hint=_setup_hint))
        results.append(mutator_result)
        mutator_present = mutator_result.status == "ok"

    if _wanted("hub"):
        severity, caps = CHECK_SPECS["hub"]
        results.append(_safe("hub", severity, caps,
                             lambda: _check_hub(effective_hub, severity=severity, caps=caps,
                                                hub_mode=hub_mode)))

    if _wanted("hub_login"):
        severity, caps = CHECK_SPECS["hub_login"]
        results.append(_safe("hub_login", severity, caps,
                             lambda: _check_hub_login(severity=severity, caps=caps,
                                                      load_creds=load_creds,
                                                      setup_hint=_setup_hint)))

    if _wanted("gh"):
        severity, caps = CHECK_SPECS["gh"]
        results.append(_safe("gh", severity, caps,
                             lambda: _check_gh(severity=severity, caps=caps, gh_state=gh_state,
                                               setup_hint=_setup_hint,
                                               install_fix=_gh_install_fix)))

    if _wanted("operator"):
        severity, caps = CHECK_SPECS["operator"]
        # operator=None -> check every registered agent (evolve picks one at
        # Start, so readiness = at least one logged in); a single name narrows.
        ops = OPERATORS if operator is None else (operator,)
        results.append(_safe("operator", severity, caps,
                             lambda: _check_operator(ops, severity=severity, caps=caps,
                                                     mutator_present=mutator_present,
                                                     preflight_operator=preflight_operator,
                                                     opencode2_keyed=opencode2_keyed,
                                                     setup_hint=_setup_hint)))

    if _wanted("rosetta"):
        severity, caps = CHECK_SPECS["rosetta"]
        results.append(_safe("rosetta", severity, caps,
                             lambda: _check_rosetta(severity=severity, caps=caps,
                                                    rosetta=rosetta_probe)))
    return results


def capability_ready(results: list[CheckResult], cap: str) -> bool:
    """Is ``cap`` ready, given ``results``?

    A capability with at least one HARD check is gated purely by those
    (every one must be ``"ok"``) -- a soft check tagged onto the same
    capability, if any, never enters into it: "a soft check failing does
    not make a hard-gated capability unready" (spec S5.6/INV6). A
    capability with NO hard checks at all (``publish``, in the real 8-check
    table) falls back to its own soft checks instead, where only a
    ``"fail"`` gates it -- a soft ``"warn"`` never gates anything, for any
    capability (INV6's "soft warnings never flip it")."""
    tagged = [r for r in results if cap in r.capabilities]
    hard = [r for r in tagged if r.severity == "hard"]
    if hard:
        return all(r.status == "ok" for r in hard)
    return all(r.status != "fail" for r in tagged)


def exit_code(results: list[CheckResult], capability: str | None) -> int:
    """Pure fold over ``results``: 0 iff ``capability`` (or ``"eval"`` when
    ``None`` -- the minimum useful default, spec S5.6) is ready, else 1. No
    I/O, no side effect -- exactly the function the test suite table-tests
    exhaustively across hard-fail/soft-warn/soft-fail combinations."""
    cap = capability if capability is not None else "eval"
    return 0 if capability_ready(results, cap) else 1


def to_json(results: list[CheckResult]) -> dict:
    """``{checks, capabilities, env}`` -- spec S5.6's complete bug-report
    shape for ``doctor -o json``. ``env`` is ``version_info()`` (the same
    builder ``--version`` uses) plus the three machine-identifying fields
    ``--version`` deliberately omits (it reports pins, offline; these are
    live facts about the machine actually running this)."""
    return {
        "checks": [
            {"id": r.id, "status": r.status, "severity": r.severity, "detail": r.detail,
             "fix": r.fix, "capabilities": list(r.capabilities)}
            for r in results
        ],
        "capabilities": {cap: capability_ready(results, cap) for cap in CAPABILITIES},
        "env": {**version_info(), "os": platform.system(), "arch": platform.machine(),
               "python": platform.python_version()},
    }


_SHA256_RE = re.compile(r"@sha256:[0-9a-f]{64}")
_FINGERPRINT_TAG_RE = re.compile(r":h-[0-9a-f]{64}")
_DIGEST_PREFIX_LEN = 19  # matches cli.py:_short_pin's prefix length exactly


def _short_digest(text: str) -> str:
    """Shorten every ``@sha256:<64 lowercase-hex>`` substring found in
    ``text`` to ``@sha256:<first 19 hex>…``, leaving all surrounding text
    untouched -- display-only. A pinned-digest image ref (``present —
    ghcr.io/dunnolab/nethackers-arena@sha256:<64 hex>``) is ~147 characters
    in ``CheckResult.detail``, breaking column alignment on any normal
    terminal; that's the DEFAULT experience for every installed (non-repo)
    user, since a repo checkout's local dev tags are short and never trigger
    this. A checkout's mutator fingerprint tag (``:h-<64 hex>``) is shortened
    the same way. Applied ONLY at render time (``render_human``/
    ``render_plain`` below) -- never to ``CheckResult.detail`` itself and
    never to ``to_json``, which must always carry the full, unmodified digest
    (``hubclient/output.py``'s own "never a stringified table" rule).
    ``cli.py:_short_pin`` (``--version``'s equivalent truncation) reuses this
    as its single source of the 19-char prefix length, so the two can never
    drift apart."""
    keep = len("@sha256:") + _DIGEST_PREFIX_LEN
    text = _SHA256_RE.sub(lambda m: m.group()[:keep] + "…", text)
    keep_tag = len(":h-") + _DIGEST_PREFIX_LEN
    return _FINGERPRINT_TAG_RE.sub(lambda m: m.group()[:keep_tag] + "…", text)


_GLYPH = {"ok": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
_PLAIN_GLYPH = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
_CAP_LABEL = {
    "eval": "eval / run a bot", "evolve": "evolve", "publish": "publish wins",
    "browse": "browse the hub",
}


def render_human(results: list[CheckResult]) -> str:
    """Grouped by capability (spec S5.6): every check tagged to a capability
    is shown under it -- so e.g. ``container_runtime``/``arena_image``
    appear under BOTH "to eval" and "to evolve" (intentional duplication:
    each block is meant to stand alone as "is my machine set up to do
    X?"). Each row is a glyph + the detail + (when not ok) its one-line
    fix; each block ends in a verdict line. Rich markup -- render through a
    ``rich`` ``Console`` (``hubclient.output.emit``'s ``table=`` path
    already does, via ``Console.print``'s own markup support)."""
    lines: list[str] = []
    for cap in CAPABILITIES:
        rows = [r for r in results if cap in r.capabilities]
        if not rows:
            continue
        lines.append(f"[b]to {_CAP_LABEL[cap]}[/]")
        for r in rows:
            if r.items:  # a check with a per-item breakdown -> parent + sublist
                lines.append(f"  {_GLYPH[r.status]} {r.id}")
                for it in r.items:
                    lines.append(f"      {_GLYPH[it.status]} {it.label}: {it.detail}")
                if r.fix and r.status != "ok":
                    lines.append(f"      [dim]→ {r.fix}[/]")
                continue
            line = f"  {_GLYPH[r.status]} {r.id:<17} {_short_digest(r.detail)}"
            if r.fix and r.status != "ok":
                line += f"  [dim]→ {r.fix}[/]"
            lines.append(line)
        verdict = _GLYPH["ok"] if capability_ready(results, cap) else _GLYPH["fail"]
        lines.append(f"ready to {cap} {verdict}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_plain(results: list[CheckResult]) -> str:
    """``render_human``'s no-markup counterpart, for ``-o plain``."""
    lines: list[str] = []
    for cap in CAPABILITIES:
        rows = [r for r in results if cap in r.capabilities]
        if not rows:
            continue
        lines.append(f"to {cap}:")
        for r in rows:
            if r.items:  # a check with a per-item breakdown -> parent + sublist
                lines.append(f"  [{_PLAIN_GLYPH[r.status]}] {r.id}")
                for it in r.items:
                    lines.append(f"        [{_PLAIN_GLYPH[it.status]}] {it.label}: {it.detail}")
                if r.fix and r.status != "ok":
                    lines.append(f"        -> {r.fix}")
                continue
            line = f"  [{_PLAIN_GLYPH[r.status]}] {r.id}: {_short_digest(r.detail)}"
            if r.fix and r.status != "ok":
                line += f" -> {r.fix}"
            lines.append(line)
        ready = "yes" if capability_ready(results, cap) else "no"
        lines.append(f"ready to {cap}: {ready}")
        lines.append("")
    return "\n".join(lines).rstrip()
