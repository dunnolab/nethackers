"""``nethackers setup``: check → plan → confirm → run → re-check → report.

Everything this touches is behind ``SetupDeps``, built by cli.py from the real
functions and by tests from fakes, so no test shells out, logs in, or
downloads anything. Modes (spec §4.7): interactive means stdin and stderr are
both terminals. Interactive: show the plan, ask once (``--yes`` skips the
question), run every step. Not interactive: print the plan and change nothing;
with ``--yes``, run the unattended steps and list the logins as commands. That
list goes to the console (stderr) like the plan: a coding agent's stdout is a
pipe, so what it finds there is doctor's JSON. A plan with nothing for
nethackers to run -- only instructions for the person -- never asks.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from nethackers.containers import RuntimeReport
from nethackers.diagnostics import CAPABILITIES, CheckResult, capability_ready, exit_code
from nethackers.hubclient.credentials import Credentials
from nethackers.operators import DEFAULT_OPERATOR
from nethackers.setup import host, render
from nethackers.setup.host import HostFacts
from nethackers.setup.plan import (
    NEEDS_TERMINAL,
    Plan,
    Situation,
    Step,
    build_plan,
    in_scope,
    listed_command,
)
from nethackers.setup.runner import StepResult
from nethackers.setup.support import mb_text, shown_command

EXAMPLE_OBJECTIVE = "val-dwa-law-fem"

LABELS = {"container_runtime": "container runtime", "arena_image": "arena image",
          "mutator_image": "mutator image", "hub": "hub", "hub_login": "hub login",
          "gh": "gh", "operator": "coding agent", "rosetta": "Rosetta"}


@dataclass(frozen=True)
class SetupOptions:
    scope: str | None      # --for
    operator: str | None   # --operator
    yes: bool              # --yes
    interactive: bool      # stdin and stderr are terminals
    hub: str


@dataclass(frozen=True)
class SetupDeps:
    console: Console
    run_checks: Callable[..., list[CheckResult]]
    detect_host: Callable[[], HostFacts]
    probe_runtime: Callable[[], RuntimeReport]
    gh_state: Callable[[], tuple[str | None, str]]
    load_creds: Callable[[], Credentials | None]
    agent_logged_in: Callable[[str], bool]
    resolve_exe: Callable[[str], str | None]
    which: Callable[[str], str | None]        # shutil.which: on this shell's PATH
    read_text: Callable[[Path], str | None]
    hub_login: Callable[[], str]
    pull: Callable[[tuple[str, ...], int | None], str | None]
    pull_size: Callable[[tuple[str, ...]], int | None]
    run_terminal: Callable[[tuple[str, ...]], StepResult]
    run_captured: Callable[[tuple[str, ...], str], StepResult]
    ask_agent: Callable[[], str]
    confirm: Callable[[], bool]
    report: Callable[[list[CheckResult], render.Summary], None]


def resolve_exe(name: str, *, which: Callable[[str], str | None], home: Path) -> str | None:
    """``name`` on PATH, else where the vendors' installers put it (~/.local/bin),
    which a fresh install may not have on this shell's PATH yet."""
    found = which(name)
    if found:
        return found
    local = home / ".local" / "bin" / name
    return str(local) if local.exists() else None


def setup_exit_code(checks: list[CheckResult], scope: str | None) -> int:
    """doctor's fold for ``--for``; with no scope, 0 only when every
    capability is ready."""
    if scope is not None:
        return exit_code(checks, scope)
    return 0 if all(capability_ready(checks, cap) for cap in CAPABILITIES) else 1


def choose_agent(opts: SetupOptions, deps: SetupDeps,
                 logged_in: Mapping[str, bool]) -> str | None:
    """The coding agent to set up: ``--operator``; else one already logged in;
    else ask (interactive, no ``--yes``); else ``None`` (left out, with a note)."""
    if opts.operator:
        return opts.operator
    for op in ("claude", "codex"):
        if logged_in.get(op):
            return op
    if opts.interactive and not opts.yes:
        return deps.ask_agent()
    return None


def run_setup(opts: SetupOptions, deps: SetupDeps) -> int:
    say = deps.console.print
    say("Checking this machine…")  # before the checks: they take a few seconds (hub, registry)
    checks = deps.run_checks(operator=opts.operator, hub=opts.hub)
    facts = deps.detect_host()
    plat = host.platform_for(facts)
    if plat is None:
        say(f"[yellow]{escape(host.NOT_COVERED)}[/]")
        deps.report(checks, _summary(checks, opts, None, agent=None))
        return 1
    wanted = in_scope(checks, opts.scope)
    evolve = opts.scope in (None, "evolve")
    logged_in = {op: deps.agent_logged_in(op) for op in ("claude", "codex")} if evolve else {}
    for line in render.checklist(_rows(wanted, logged_in)):
        say(line)
    agent = choose_agent(opts, deps, logged_in) if evolve else None
    runtime = deps.probe_runtime()
    gh_login, gh_state = deps.gh_state()
    creds = deps.load_creds()
    pulls = tuple(k for k in ("arena", "mutator")
                  if f"{k}_image" in wanted and wanted[f"{k}_image"].status != "ok")
    emulation = plat.emulation(facts, read_text=deps.read_text)[2] if "rosetta" in wanted else None
    sit = Situation(
        checks=tuple(checks), facts=facts, runtime=runtime, scope=opts.scope, agent=agent,
        agent_installed=agent is not None and (agent == "opencode2"
                                               or deps.resolve_exe(agent) is not None),
        agent_logged_in=agent is not None and (agent == "opencode2"
                                               or deps.agent_logged_in(agent)),
        hub_login=creds.login if creds is not None else None,
        gh_login=gh_login, gh_state=gh_state,
        pull_size=deps.pull_size(pulls) if pulls and runtime.runtime is not None else None,
        emulation=emulation,
    )
    plan = build_plan(sit, plat)

    def finish(checks: list[CheckResult]) -> int:
        deps.report(checks, _summary(checks, opts, plan, agent=agent))
        return setup_exit_code(checks, opts.scope)

    if plan.empty:
        return finish(checks)
    for line in render.plan_lines(plan, machine=_machine(facts)):
        say(line)
    if not plan.steps:  # only instructions: a yes (or --yes) would run nothing
        return finish(checks)
    if not opts.interactive and not opts.yes:
        say("[dim]Nothing changed. Run `nethackers setup --yes` to apply this plan, "
            "or run it in a terminal.[/]")
        return finish(checks)
    if opts.interactive and not opts.yes and not deps.confirm():
        say("[dim]Nothing changed.[/]")
        return finish(checks)
    runnable = [s for s in plan.steps if opts.interactive or s.kind not in NEEDS_TERMINAL]
    _run_steps(runnable, plan, deps, sit)
    logins = [] if opts.interactive else [_listed_command(s, deps) for s in plan.steps
                                          if s.kind in NEEDS_TERMINAL]
    if logins:
        for line in render.listed_logins(logins):
            say(line, soft_wrap=True)  # a command to copy: never broken across lines
    return finish(deps.run_checks(operator=agent or opts.operator, hub=opts.hub))


def _run_steps(steps: list[Step], plan: Plan, deps: SetupDeps,
               sit: Situation) -> dict[str, StepResult]:
    say = deps.console.print
    titles = {s.id: s.title for s in plan.steps}
    results: dict[str, StepResult] = {}
    for n, step in enumerate(steps, 1):
        missing = [titles.get(i, i) for i in step.needs if not (i in results and results[i].ok)]
        if missing:
            result = StepResult(False, 0.0, f"needs: {', '.join(missing)}", skipped=True)
        else:
            say(f"[b][{n}/{len(steps)}] {escape(step.title)}[/]")
            result = _run_one(step, deps, sit)
        results[step.id] = result
        say(render.result_line(step, result))
        for line in result.tail if not result.ok else ():
            say(f"      [dim]{escape(line)}[/]")
    return results


def _run_one(step: Step, deps: SetupDeps, sit: Situation) -> StepResult:
    start = time.monotonic()
    try:
        if step.kind == "hub_login":
            return StepResult(True, time.monotonic() - start, f"@{deps.hub_login()}")
        if step.kind == "terminal":
            exe = deps.resolve_exe(step.argv[0])
            if exe is None:
                return StepResult(False, 0.0, f"`{step.argv[0]}` isn't on PATH — open a new "
                                              "terminal, then run `nethackers setup` again")
            return deps.run_terminal((exe, *step.argv[1:]))
        if step.kind == "captured":
            return deps.run_captured(step.argv, step.title)
        if step.kind == "same_account":
            return _same_account(deps)
        if step.kind == "pull":
            error = deps.pull(step.images, sit.pull_size)
            if error is not None:
                return StepResult(False, time.monotonic() - start, error)
            size = mb_text(sit.pull_size) if sit.pull_size else ""
            return StepResult(True, time.monotonic() - start, size)
    except Exception as exc:  # a failed step never stops the others
        return StepResult(False, time.monotonic() - start, f"{type(exc).__name__}: {exc}")
    raise ValueError(f"unknown step kind {step.kind!r}")


def _same_account(deps: SetupDeps) -> StepResult:
    creds = deps.load_creds()
    gh, _state = deps.gh_state()
    hub = creds.login if creds is not None else None
    if hub and gh and hub == gh:
        return StepResult(True, 0.0, f"@{gh}")
    return StepResult(False, 0.0, f"gh is @{gh or '?'} but the hub login is @{hub or '?'} — "
                                  "run `gh auth switch` or `nethackers login` again")


def _listed_command(step: Step, deps: SetupDeps) -> str:
    """A login setup didn't run (no terminal), as a command to run instead. A
    tool its vendor's installer just put in ~/.local/bin isn't on this shell's
    PATH yet, so then the command names the file itself."""
    if step.kind == "terminal" and deps.which(step.argv[0]) is None:
        exe = deps.resolve_exe(step.argv[0])
        if exe is not None:
            return shown_command((exe, *step.argv[1:]))
    return listed_command(step)


def _machine(facts: HostFacts) -> str:
    return "Mac" if facts.system == "Darwin" else (facts.distro_name or "Linux machine")


def _row(r: CheckResult) -> render.Row:
    """doctor's result, shortened for the checklist."""
    if r.id in ("arena_image", "mutator_image"):
        if r.status == "ok":
            return render.Row("ok", LABELS[r.id], "present")
        return render.Row("fail", LABELS[r.id],
                          "not pulled yet" if r.status == "warn" else "can't reach the registry")
    if r.id == "container_runtime" and r.status != "ok":
        detail = ("none installed" if r.detail == "no docker or podman found on PATH"
                  else r.detail[:70])
        return render.Row("fail", LABELS[r.id], detail)
    if r.id == "rosetta":
        return render.Row("warn", LABELS[r.id], "off: amd64 runs under QEMU")
    detail = r.detail.replace("logged in as ", "").replace("authed as ", "")
    return render.Row(r.status, LABELS[r.id], detail)


def _rows(wanted: dict[str, CheckResult], logged_in: Mapping[str, bool]) -> list[render.Row]:
    rows: list[render.Row] = []
    for cid, result in wanted.items():
        if cid == "operator" or (cid == "hub" and result.status == "ok"):
            continue
        if cid == "rosetta" and result.status != "warn":
            continue
        rows.append(_row(result))
    images = [r for r in rows if r.label in ("arena image", "mutator image")]
    if len(images) == 2 and (images[0].status, images[0].detail) == (images[1].status,
                                                                     images[1].detail):
        at = rows.index(images[0])
        rows = [r for r in rows if r not in images]
        rows.insert(at, render.Row(images[0].status, "sandbox images", images[0].detail))
    if logged_in:
        ready = [op for op, ok in logged_in.items() if ok]
        rows.append(render.Row("ok", "coding agent", f"{ready[0]}, logged in") if ready
                    else render.Row("fail", "coding agent", "claude and codex aren't logged in"))
    return rows


def _summary(checks: list[CheckResult], opts: SetupOptions, plan: Plan | None, *,
             agent: str | None) -> render.Summary:
    caps = CAPABILITIES if opts.scope is None else (opts.scope,)
    ready = tuple(c for c in caps if capability_ready(checks, c))
    not_ready = tuple(c for c in caps if c not in ready)
    wanted = in_scope(checks, opts.scope)
    failing = tuple((LABELS[r.id], r.detail) for r in wanted.values()
                    if (r.severity == "hard" and r.status != "ok") or r.status == "fail")
    next_command = None
    if "evolve" in caps and capability_ready(checks, "evolve"):
        next_command = (f"nethackers evolve {EXAMPLE_OBJECTIVE} --seed autoascend "
                        f"--operator {agent or DEFAULT_OPERATOR}")
    elif "eval" in caps and capability_ready(checks, "eval"):
        next_command = f"nethackers eval ./my-bot --objective {EXAMPLE_OBJECTIVE}"
    return render.Summary(
        ready=ready, not_ready=not_ready, failing=failing,
        yours=plan.yours if plan is not None else (),
        afterwards=plan.afterwards if plan is not None else (),
        next_command=next_command, nothing_to_do=plan is not None and plan.empty)
