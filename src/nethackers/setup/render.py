"""How setup's output reads: the checklist, the plan, one line per finished
step, and the final summary. Pure functions -- data in, rich markup out -- so
the look is testable without running anything. ``summary_plain`` is the
``-o plain`` variant."""
from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape

from nethackers.diagnostics import _short_digest
from nethackers.setup.plan import NEEDS_TERMINAL, Plan, Step, Todo
from nethackers.setup.runner import StepResult, elapsed_text
from nethackers.setup.support import NotCovered, Support, Untested

_GLYPH = {"ok": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
_WORD = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}


@dataclass(frozen=True)
class Row:
    status: str   # "ok" | "warn" | "fail"
    label: str
    detail: str


@dataclass(frozen=True)
class Summary:
    ready: tuple[str, ...]
    not_ready: tuple[str, ...]
    failing: tuple[tuple[str, str], ...]   # (check label, detail) still failing in scope
    yours: tuple[Todo, ...]
    afterwards: tuple[Todo, ...]
    commands: tuple[str, ...]              # logins left for someone to run themselves
    next_command: str | None
    nothing_to_do: bool = False


def checklist(rows: list[Row]) -> list[str]:
    """One line per check, under the "Checking this machine…" header that
    ``flow`` prints before the (slow) checks start."""
    return [f"  {_GLYPH[r.status]} {r.label:<19} {escape(r.detail)}" for r in rows]


def _marker(support: Support | None) -> str:
    if isinstance(support, Untested):
        return "  [dim](untested)[/]"
    if isinstance(support, NotCovered):
        return "  [dim](not covered)[/]"
    return ""


def _untested(plan: Plan) -> bool:
    supports: list[Support | None] = [s.recipe.support for s in plan.steps
                                      if s.recipe is not None]
    supports += [t.support for t in (*plan.yours, *plan.afterwards)]
    return any(isinstance(s, Untested) for s in supports)


def _who_line(steps: tuple[Step, ...]) -> str:
    keys = [str(n) for n, step in enumerate(steps, 1) if step.kind in NEEDS_TERMINAL]
    if not keys:
        who = "Every step runs on its own."
    elif len(keys) == len(steps):
        who = "Every step needs you at the keyboard."
    elif len(keys) == 1:
        who = f"Step {keys[0]} needs you at the keyboard; the rest run on their own."
    else:
        who = f"Steps {', '.join(keys)} need you at the keyboard; the rest run on their own."
    return f"{who} Nothing nethackers runs needs sudo."


def plan_lines(plan: Plan, *, machine: str) -> list[str]:
    out: list[str] = []
    if plan.steps:
        out += ["", "nethackers will:"]
        # Wide enough for every title in THIS plan -- a fixed guess (34) fit
        # every OS-recipe title but not plan.py's own "check gh and the hub
        # are one account" (36), which then ran straight into the command
        # column with no gap at all.
        width = max(len(step.title) for step in plan.steps)
        for n, step in enumerate(plan.steps, 1):
            marker = _marker(step.recipe.support) if step.recipe is not None else ""
            out.append(f"  {n}  {step.title:<{width}} [cyan]{escape(step.shows)}[/]{marker}")
    if plan.yours:
        out += ["", "You'll need to (nethackers never runs sudo):"]
        out += [f"  • {escape(t.say)}{_marker(t.support)}" for t in plan.yours]
    if plan.afterwards:
        out += ["", "Afterwards, only you can do this:"]
        out += [f"  • {escape(t.say)}{_marker(t.support)}" for t in plan.afterwards]
    if plan.notes:
        out.append("")
        out += [f"[dim]{escape(note)}[/]" for note in plan.notes]
    if plan.steps:
        out.append(_who_line(plan.steps))
    if _untested(plan):
        out.append("Steps marked (untested) come from vendor docs and haven't been run on a "
                   f"real {machine} yet.")
    return out


def result_line(step: Step, result: StepResult) -> str:
    if result.skipped:
        return f"  [dim]– {step.title}: skipped ({escape(result.detail)})[/]"
    if not result.ok:
        return f"  {_GLYPH['fail']} {step.title} — {escape(result.detail)}"
    took = elapsed_text(result.seconds)
    if step.kind == "pull" and result.detail:
        return f"  {_GLYPH['ok']} {step.title}   [dim]{escape(result.detail)} in {took}[/]"
    extra = f"   {escape(result.detail)}" if result.detail else ""
    return f"  {_GLYPH['ok']} {step.title}   [dim]{took}[/]{extra}"


def _summary_lines(s: Summary, glyph: dict[str, str], esc) -> list[str]:
    lines: list[str] = []
    if not s.not_ready:
        tail = " Nothing to do." if s.nothing_to_do else ""
        lines.append(f"{glyph['ok']} ready to {' · '.join(s.ready)}.{tail}")
    else:
        if s.ready:
            lines.append(f"{glyph['ok']} ready to {' · '.join(s.ready)}")
        lines.append(f"{glyph['fail']} not ready to {' · '.join(s.not_ready)}")
        # A pinned image ref's raw detail (arena_image/mutator_image) runs to
        # ~150 characters -- doctor's own render shortens it the same way
        # (diagnostics._short_digest) so it doesn't hard-wrap mid-digest here.
        lines += [f"  {label}: {esc(_short_digest(detail))}" for label, detail in s.failing]
    if s.commands:
        lines.append("Run these logins yourself (each prints a code or a link to open):")
        lines += [f"  {esc(c)}" for c in s.commands]
    if s.yours:
        lines.append("Still to do (nethackers never runs sudo):")
        lines += [f"  • {esc(t.say)}" for t in s.yours]
    if s.afterwards:
        lines.append("Afterwards:")
        lines += [f"  • {esc(t.say)}" for t in s.afterwards]
    if s.not_ready or s.yours or s.commands:
        lines.append("Then run `nethackers setup` again. It picks up where it left off.")
    if s.next_command:
        lines.append(f"Next: {esc(s.next_command)}")
    return lines


def summary_markup(s: Summary) -> str:
    return "\n".join(_summary_lines(s, _GLYPH, escape))


def summary_plain(s: Summary) -> str:
    return "\n".join(_summary_lines(s, _WORD, lambda text: text))
