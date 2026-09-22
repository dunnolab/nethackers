"""Turns what setup found into an ordered plan -- pure: no probing, no I/O.

The order is the spec's D4: every step that needs the person (the logins)
comes before the slow ones (installing and starting a runtime, pulling
images), so they can walk away once the logins are done; the hub login comes
before gh's so the same-account check can run. A step lists the steps it
``needs``; the runner skips it, with the reason, when one of those didn't
succeed. What only the person can do (anything needing sudo, a GUI click, or
a decision between two accounts) becomes a ``Todo``, printed, never run.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Literal

from nethackers.containers import RuntimeReport
from nethackers.diagnostics import CAPABILITIES, CheckResult
from nethackers.setup.host import HostFacts
from nethackers.setup.support import Recipe, Support, mb_text, shown_command

StepKind = Literal["hub_login", "terminal", "captured", "same_account", "pull"]

GH_LOGIN: tuple[str, ...] = ("gh", "auth", "login", "--hostname", "github.com",
                             "--git-protocol", "https", "--web")
AGENT_LOGIN: dict[str, tuple[str, ...]] = {"claude": ("claude", "auth", "login"),
                                           "codex": ("codex", "login")}
AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "opencode2": "OpenCode"}
NEEDS_TERMINAL = frozenset({"hub_login", "terminal"})


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    kind: StepKind
    shows: str                        # the plan's right-hand column
    argv: tuple[str, ...] = ()
    recipe: Recipe | None = None      # the OS recipe behind it, if any (for its status)
    needs: tuple[str, ...] = ()       # ids of steps that must succeed first
    images: tuple[str, ...] = ()      # pull steps: "arena", "mutator"


@dataclass(frozen=True)
class Todo:
    """Something only the person can do; printed, never run."""

    does: str
    say: str
    support: Support | None = None    # None: not an OS recipe (e.g. an account mismatch)


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...] = ()
    yours: tuple[Todo, ...] = ()        # printed before the run; blocks something
    afterwards: tuple[Todo, ...] = ()   # advice for after the run (Rosetta)
    notes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.steps or self.yours or self.afterwards or self.notes)


@dataclass(frozen=True)
class Situation:
    """Everything planning needs, gathered by ``flow`` so this module stays pure."""

    checks: tuple[CheckResult, ...]
    facts: HostFacts
    runtime: RuntimeReport
    scope: str | None                 # --for, or None for every capability
    agent: str | None                 # the coding agent to set up; None = leave it out
    agent_installed: bool
    agent_logged_in: bool
    hub_login: str | None             # the stored hub login
    gh_login: str | None              # gh's account, None if missing or logged out
    gh_state: str                     # "authed" | "unauthed" | "missing"
    pull_size: int | None = None      # bytes the pull will download, when known
    emulation: Recipe | None = None   # what turns Rosetta on, when it's off


def in_scope(checks: Sequence[CheckResult], scope: str | None) -> dict[str, CheckResult]:
    """doctor's results for the capabilities in scope, by check id."""
    caps = CAPABILITIES if scope is None else (scope,)
    return {r.id: r for r in checks if any(c in r.capabilities for c in caps)}


def listed_command(step: Step) -> str:
    """How a login is printed for someone to run themselves."""
    if step.kind == "hub_login":
        return "nethackers login"
    return shown_command(step.argv)


def _todo(recipe: Recipe) -> Todo:
    return Todo(recipe.does, recipe.say, recipe.support)


def _recipe_step(step_id: str, recipe: Recipe, needs: tuple[str, ...] = ()) -> Step:
    return Step(step_id, recipe.does, "captured", shown_command(recipe.argv), recipe.argv,
                recipe, needs)


def _not_ok(wanted: dict[str, CheckResult], check_id: str) -> bool:
    return check_id in wanted and wanted[check_id].status != "ok"


def build_plan(sit: Situation, plat: ModuleType) -> Plan:
    """The ordered plan for ``sit`` on the OS ``plat`` (``setup.macos`` /
    ``setup.linux``)."""
    wanted = in_scope(sit.checks, sit.scope)
    steps: list[Step] = []
    yours: list[Todo] = []
    notes: list[str] = []

    hub_planned = _not_ok(wanted, "hub_login")
    if hub_planned:
        steps.append(Step("hub-login", "log you in to the hub", "hub_login",
                          "nethackers login (a GitHub code, in your browser)"))

    gh_planned = False
    if "gh" in wanted and sit.gh_state != "authed":
        can_log_in = sit.gh_state == "unauthed"
        gh_needs: tuple[str, ...] = ()
        if sit.gh_state == "missing":
            recipe = plat.gh_install_recipe(sit.facts)
            if recipe.who == "nethackers":
                steps.append(_recipe_step("gh-install", recipe))
                can_log_in, gh_needs = True, ("gh-install",)
            else:
                yours.append(_todo(recipe))
        if can_log_in:
            steps.append(Step("gh-login", "log you in to gh", "terminal",
                              shown_command(GH_LOGIN), GH_LOGIN, needs=gh_needs))
            gh_planned = True

    if "gh" in wanted and "hub_login" in wanted:
        hub_after = sit.hub_login is not None or hub_planned
        gh_after = sit.gh_login is not None or gh_planned
        if (hub_planned or gh_planned) and hub_after and gh_after:
            needs = tuple(step_id for step_id, planned in
                          (("hub-login", hub_planned), ("gh-login", gh_planned)) if planned)
            steps.append(Step("same-account", "check gh and the hub are one account",
                              "same_account", "compares the two GitHub logins", needs=needs))
        elif sit.hub_login and sit.gh_login and sit.hub_login != sit.gh_login:
            yours.append(Todo(
                "use one GitHub account",
                f"gh is @{sit.gh_login} but the hub login is @{sit.hub_login}: "
                "run `gh auth switch` or `nethackers login` again"))

    if sit.scope in (None, "evolve"):
        if sit.agent is None:
            notes.append("No coding agent set up yet: run "
                         "`nethackers setup --operator claude|codex|opencode2`.")
        else:
            agent_needs: tuple[str, ...] = ()
            if not sit.agent_installed:
                recipe = plat.agent_install_recipe(sit.facts, sit.agent)
                if recipe is not None:
                    steps.append(_recipe_step("agent-install", recipe))
                    agent_needs = ("agent-install",)
            login = AGENT_LOGIN.get(sit.agent)
            if login is not None and not sit.agent_logged_in:
                steps.append(Step("agent-login", f"log you in to {AGENT_NAMES[sit.agent]}",
                                  "terminal", shown_command(login), login, needs=agent_needs))

    runtime_steps: list[str] = []
    runtime_blocked = False
    if _not_ok(wanted, "container_runtime"):
        for recipe in plat.runtime_recipes(sit.facts, sit.runtime):
            if recipe.who == "nethackers":
                step_id = f"runtime-{len(runtime_steps) + 1}"
                steps.append(_recipe_step(step_id, recipe, tuple(runtime_steps[-1:])))
                runtime_steps.append(step_id)
            else:
                yours.append(_todo(recipe))
                runtime_blocked = True

    pulls = tuple(kind for kind in ("arena", "mutator") if _not_ok(wanted, f"{kind}_image"))
    runtime_ready = sit.runtime.runtime is not None or (bool(runtime_steps)
                                                        and not runtime_blocked)
    if pulls and runtime_ready:
        # The registry's size counts every layer; layers already here (an
        # older pin's) are skipped, so the real download can only be smaller.
        size = f"up to {mb_text(sit.pull_size)}" if sit.pull_size else ""
        title = "pull the sandbox images" if len(pulls) == 2 else f"pull the {pulls[0]} image"
        steps.append(Step("pull", title, "pull", size, needs=tuple(runtime_steps[-1:]),
                          images=pulls))

    advice = _todo(sit.emulation) if sit.emulation is not None and "rosetta" in wanted else None
    # Already a step before the run (installing Rosetta holds back a new VM): say it once.
    afterwards = (advice,) if advice is not None and advice not in yours else ()
    return Plan(tuple(steps), tuple(yours), afterwards, tuple(notes))
