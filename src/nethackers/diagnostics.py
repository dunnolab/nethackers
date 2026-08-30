"""Diagnostics: the version regimes ``nethackers`` reports about itself, and
per-capability machine readiness (spec ``docs/superpowers/specs/2026-08-28-
sandbox-image-distribution-design.md`` 5.7/5.6).

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
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version
from pathlib import Path

from nethackers import _image_pins
from nethackers.config import load_stage
from nethackers.harness import sandbox_preflight
from nethackers.harness.version import RUN_SCHEMA_VERSION
from nethackers.hubclient.client import HubClient, HubUnreachable
from nethackers.hubclient.credentials import Credentials, load as _default_load_creds
from nethackers.hubclient.publish import gh_state as _default_gh_state


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
# 7-row table a third time.
CHECK_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "container_runtime": ("hard", ("eval", "evolve")),
    "arena_image": ("hard", ("eval", "evolve")),
    "mutator_image": ("hard", ("evolve",)),
    "hub": ("soft", ("browse", "publish")),
    "hub_login": ("soft", ("publish",)),
    "gh": ("soft", ("publish",)),
    "operator": ("hard", ("evolve",)),
}


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


def _manifest_reachable(ref: str, *, run=subprocess.run) -> bool:
    """Cheap existence probe for an image ref that isn't present locally yet:
    ``docker manifest inspect`` hits the registry's manifest endpoint
    without pulling any layers, so a not-yet-pulled-but-publishable ref
    ("pullable") can be told apart from one the registry doesn't have / a
    registry that can't be reached at all ("unreachable") -- spec S5.6.
    ``False`` on ANY failure (404, stale auth, network down, docker itself
    missing): distinguishing *why* isn't doctor's job here -- ``ensure_
    image``'s own error mapping already covers that at actual-pull time."""
    try:
        result = run(["docker", "manifest", "inspect", ref], capture_output=True, timeout=10)
        return bool(result.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return False


def _default_hub_mode(hub: str) -> str | None:
    """The exact call ``whoami`` makes (``cli.py``'s handler) --
    ``HubClient(hub).hub_mode()`` -- so the ``hub`` check shares one source
    of truth with it (INV5). May raise ``HubUnreachable``; ``_check_hub`` is
    the only place that catches it."""
    return HubClient(hub).hub_mode()


def _check_container_runtime(
    *, severity: str, caps: tuple[str, ...], docker_available: Callable[[], bool],
) -> CheckResult:
    if docker_available():
        return CheckResult(id="container_runtime", status="ok", severity=severity,
                           detail="a working container runtime is available",
                           fix=None, capabilities=caps)
    return CheckResult(id="container_runtime", status="fail", severity=severity,
                       detail="no working container runtime found",
                       fix=sandbox_preflight.sandbox_hint(), capabilities=caps)


def _check_image(
    kind: str,
    *,
    severity: str,
    caps: tuple[str, ...],
    resolve_image: Callable[[str | None, str], str],
    image_present: Callable[[str], bool],
    manifest_reachable: Callable[[str], bool],
    repo_root: Callable[[], Path | None],
) -> CheckResult:
    """``present`` (already local) / ``warn``-``pullable`` (not local, but
    the registry has it -- ``nethackers doctor --pull`` fetches it) /
    ``fail``-``unreachable`` (neither) -- spec S5.6. The "unreachable" fix
    text branches on whether this is actually a repo checkout
    (``repo_root() is not None``) -- mirroring ``ensure_image``'s own real
    branch order -- rather than inferring that from the ref's shape: a repo
    checkout can build locally (``make``, which ``eval``/``evolve`` also run
    automatically) regardless of what ref happens to be resolved; outside a
    repo (the installed-user path, or a hand-set ``NETHACKERS_*_IMAGE``
    override with no checkout to build from) the only path is the
    network/registry, so the fix names that instead."""
    check_id = f"{kind}_image"
    ref = resolve_image(None, kind)
    if image_present(ref):
        return CheckResult(id=check_id, status="ok", severity=severity,
                           detail=f"present — {ref}", fix=None, capabilities=caps)
    if manifest_reachable(ref):
        return CheckResult(id=check_id, status="warn", severity=severity,
                           detail=f"not local yet, but pullable — {ref}",
                           fix="run `nethackers doctor --pull` to fetch it now",
                           capabilities=caps)
    if repo_root() is not None:
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
) -> CheckResult:
    creds = load_creds()
    if creds is not None:
        return CheckResult(id="hub_login", status="ok", severity=severity,
                           detail=f"logged in as @{creds.login}", fix=None, capabilities=caps)
    return CheckResult(id="hub_login", status="fail", severity=severity, detail="not logged in",
                       fix="run `nethackers login`", capabilities=caps)


def _check_gh(
    *, severity: str, caps: tuple[str, ...], gh_state: Callable[[], tuple[str | None, str]],
) -> CheckResult:
    login, state = gh_state()
    if state == "authed":
        return CheckResult(id="gh", status="ok", severity=severity, detail=f"authed as @{login}",
                           fix=None, capabilities=caps)
    if state == "unauthed":
        return CheckResult(id="gh", status="fail", severity=severity,
                           detail="gh is installed but not authed",
                           fix="run `gh auth login` (separate from `nethackers login`)",
                           capabilities=caps)
    return CheckResult(id="gh", status="fail", severity=severity, detail="gh is not installed",
                       fix="install the GitHub CLI (`gh`), then run `gh auth login`",
                       capabilities=caps)


_OTHER_OPERATOR = {"codex": "claude", "claude": "codex"}


def _check_operator(
    operator: str, *, severity: str, caps: tuple[str, ...], mutator_present: bool,
    preflight_operator: Callable[[str], str | None],
) -> CheckResult:
    other = _OTHER_OPERATOR.get(operator, "the other operator")
    msg = preflight_operator(operator)
    if msg is None:
        detail = (f"{operator}: host login resolvable "
                  f"({other} not checked — pass --operator {other})")
        return CheckResult(id="operator", status="ok", severity=severity, detail=detail,
                           fix=None, capabilities=caps)
    if mutator_present:
        fix = f"run `{operator} login`"
    else:
        # The mutator image is what actually runs the operator, but we never
        # force a pull just to double-check a host-side login (doctor is
        # read-only outside `--pull`) -- name that option instead of silently
        # trusting an unverifiable host-only signal.
        fix = (f"run `{operator} login` -- or `nethackers doctor --pull` to pull the "
              "sandbox and verify inside it")
    return CheckResult(id="operator", status="fail", severity=severity,
                       detail=f"no resolvable {operator} login on this host", fix=fix,
                       capabilities=caps)


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
    operator: str = "codex",
    hub: str | None = None,
    docker_available: Callable[[], bool] = sandbox_preflight.docker_available,
    resolve_image: Callable[[str | None, str], str] = sandbox_preflight.resolve_image,
    image_present: Callable[[str], bool] = sandbox_preflight.image_present,
    manifest_reachable: Callable[[str], bool] = _manifest_reachable,
    repo_root: Callable[[], Path | None] = sandbox_preflight._repo_root,
    preflight_operator: Callable[[str], str | None] = sandbox_preflight.preflight_operator,
    hub_mode: Callable[[str], str | None] = _default_hub_mode,
    load_creds: Callable[[], Credentials | None] = _default_load_creds,
    gh_state: Callable[[], tuple[str | None, str]] = _default_gh_state,
) -> list[CheckResult]:
    """The 7 checks behind ``nethackers doctor`` (spec S5.6). NEVER raises,
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
    """
    effective_hub = hub if hub is not None else load_stage().hub_url
    results: list[CheckResult] = []

    severity, caps = CHECK_SPECS["container_runtime"]
    results.append(_safe("container_runtime", severity, caps,
                         lambda: _check_container_runtime(severity=severity, caps=caps,
                                                          docker_available=docker_available)))

    severity, caps = CHECK_SPECS["arena_image"]
    results.append(_safe("arena_image", severity, caps,
                         lambda: _check_image("arena", severity=severity, caps=caps,
                                              resolve_image=resolve_image,
                                              image_present=image_present,
                                              manifest_reachable=manifest_reachable,
                                              repo_root=repo_root)))

    severity, caps = CHECK_SPECS["mutator_image"]
    mutator_result = _safe("mutator_image", severity, caps,
                           lambda: _check_image("mutator", severity=severity, caps=caps,
                                                resolve_image=resolve_image,
                                                image_present=image_present,
                                                manifest_reachable=manifest_reachable,
                                                repo_root=repo_root))
    results.append(mutator_result)
    mutator_present = mutator_result.status == "ok"

    severity, caps = CHECK_SPECS["hub"]
    results.append(_safe("hub", severity, caps,
                         lambda: _check_hub(effective_hub, severity=severity, caps=caps,
                                            hub_mode=hub_mode)))

    severity, caps = CHECK_SPECS["hub_login"]
    results.append(_safe("hub_login", severity, caps,
                         lambda: _check_hub_login(severity=severity, caps=caps,
                                                  load_creds=load_creds)))

    severity, caps = CHECK_SPECS["gh"]
    results.append(_safe("gh", severity, caps,
                         lambda: _check_gh(severity=severity, caps=caps, gh_state=gh_state)))

    severity, caps = CHECK_SPECS["operator"]
    results.append(_safe("operator", severity, caps,
                         lambda: _check_operator(operator, severity=severity, caps=caps,
                                                 mutator_present=mutator_present,
                                                 preflight_operator=preflight_operator)))
    return results


def capability_ready(results: list[CheckResult], cap: str) -> bool:
    """Is ``cap`` ready, given ``results``?

    A capability with at least one HARD check is gated purely by those
    (every one must be ``"ok"``) -- a soft check tagged onto the same
    capability, if any, never enters into it: "a soft check failing does
    not make a hard-gated capability unready" (spec S5.6/INV6). A
    capability with NO hard checks at all (``publish``, in the real 7-check
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
_DIGEST_PREFIX_LEN = 19  # matches cli.py:_short_pin's prefix length exactly


def _short_digest(text: str) -> str:
    """Shorten every ``@sha256:<64 lowercase-hex>`` substring found in
    ``text`` to ``@sha256:<first 19 hex>…``, leaving all surrounding text
    untouched -- display-only. A pinned-digest image ref (``present —
    ghcr.io/dunnolab/nethackers-arena@sha256:<64 hex>``) is ~147 characters
    in ``CheckResult.detail``, breaking column alignment on any normal
    terminal; that's the DEFAULT experience for every installed (non-repo)
    user, since a repo checkout's local dev tags are short and never trigger
    this. Applied ONLY at render time (``render_human``/``render_plain``
    below) -- never to ``CheckResult.detail`` itself and never to
    ``to_json``, which must always carry the full, unmodified digest
    (``hubclient/output.py``'s own "never a stringified table" rule).
    ``cli.py:_short_pin`` (``--version``'s equivalent truncation) reuses this
    as its single source of the 19-char prefix length, so the two can never
    drift apart."""
    keep = len("@sha256:") + _DIGEST_PREFIX_LEN
    return _SHA256_RE.sub(lambda m: m.group()[:keep] + "…", text)


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
            line = f"  [{_PLAIN_GLYPH[r.status]}] {r.id}: {_short_digest(r.detail)}"
            if r.fix and r.status != "ok":
                line += f" -> {r.fix}"
            lines.append(line)
        ready = "yes" if capability_ready(results, cap) else "no"
        lines.append(f"ready to {cap}: {ready}")
        lines.append("")
    return "\n".join(lines).rstrip()
