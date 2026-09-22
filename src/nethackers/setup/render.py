"""How setup's output reads: the checklist, the plan, one line per finished
step, and the final summary. Pure functions -- data in, rich renderables (or
markup) out, nothing printed here -- so the look is testable without running
anything. ``summary_plain`` is the ``-o plain`` variant.

Columns are ``Table.grid``s, so a long command or instruction wraps inside
its own column instead of back to column 0 on an 80-column terminal. Text is
built as ``Text`` rather than left as markup strings, so rich's highlighter
never recolours numbers or digest fragments in it, whichever console prints
it (the summary goes through ``emit``'s shared stdout console).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from nethackers.diagnostics import _short_digest
from nethackers.setup.plan import NEEDS_TERMINAL, Plan, Step, Todo
from nethackers.setup.runner import StepResult, elapsed_text
from nethackers.setup.support import NotCovered, Support, Untested

_GLYPH = {"ok": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
_WORD = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
_LABEL_WIDTH = 19   # the checklist's label column: "container runtime" and a gap


@dataclass(frozen=True)
class Row:
    status: str   # "ok" | "warn" | "fail"
    label: str
    detail: str


@dataclass(frozen=True)
class Summary:
    ready: tuple[str, ...]
    not_ready: tuple[str, ...]
    failing: tuple[tuple[str, str], ...]   # (check label, short detail) still failing in scope
    yours: tuple[Todo, ...]
    afterwards: tuple[Todo, ...]
    next_command: str | None
    nothing_to_do: bool = False


def _grid() -> Table:
    return Table.grid(padding=(0, 1))


def _bullets(items: Iterable[str]) -> Table:
    """A "•" list whose wrapped lines keep their indent; ``items`` are markup."""
    grid = _grid()
    grid.add_column(justify="right", min_width=3, no_wrap=True)
    grid.add_column(overflow="fold")
    for item in items:
        grid.add_row("•", Text.from_markup(item))
    return grid


def checklist(rows: list[Row]) -> Table:
    """One row per check, under the "Checking this machine…" header that
    ``flow`` prints before the (slow) checks start."""
    grid = _grid()
    grid.add_column(justify="right", min_width=3, no_wrap=True)
    grid.add_column(min_width=_LABEL_WIDTH, no_wrap=True)
    grid.add_column(overflow="fold")
    for r in rows:
        grid.add_row(Text.from_markup(_GLYPH[r.status]), r.label, Text(r.detail))
    return grid


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


def _steps(steps: tuple[Step, ...]) -> Table:
    """Number, title, command: the command folds inside its own column, and a
    recipe's "(untested)" stays at the end of it."""
    grid = _grid()
    grid.add_column(justify="right", min_width=3, no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    for n, step in enumerate(steps, 1):
        marker = _marker(step.recipe.support) if step.recipe is not None else ""
        grid.add_row(str(n), step.title,
                     Text.from_markup(f"[cyan]{escape(step.shows)}[/]{marker}"))
    return grid


def plan_lines(plan: Plan, *, machine: str) -> list[RenderableType]:
    out: list[RenderableType] = []
    if plan.steps:
        out += [Text(), Text("nethackers will:"), _steps(plan.steps)]
    if plan.yours:
        out += [Text(), Text("You'll need to (nethackers never runs sudo):"),
                _bullets(f"{escape(t.say)}{_marker(t.support)}" for t in plan.yours)]
    if plan.afterwards:
        out += [Text(), Text("Afterwards, only you can do this:"),
                _bullets(f"{escape(t.say)}{_marker(t.support)}" for t in plan.afterwards)]
    if plan.notes:
        out.append(Text())
        out += [Text(note, style="dim") for note in plan.notes]
    if plan.steps:
        out.append(Text(_who_line(plan.steps)))
    if _untested(plan):
        out.append(Text("Steps marked (untested) come from vendor docs and haven't been run on "
                        f"a real {machine} yet."))
    return out


def result_line(step: Step, result: StepResult) -> str:
    if result.skipped:
        return f"  [dim]– {step.title}: skipped ({escape(result.detail)})[/]"
    if not result.ok:
        return f"  {_GLYPH['fail']} {step.title} — {escape(result.detail)}"
    took = elapsed_text(result.seconds)
    extra = f"   {escape(result.detail)}" if result.detail else ""
    return f"  {_GLYPH['ok']} {step.title}   [dim]{took}[/]{extra}"


def listed_logins(commands: list[str]) -> list[str]:
    """The logins setup couldn't run without a terminal, as commands to run --
    on the console, where a coding agent reads them (its stdout is a pipe)."""
    return ["", "Run these logins yourself (each prints a code or a link to open):",
            *(f"  {escape(command)}" for command in commands)]


def _failing(pairs: tuple[tuple[str, str], ...]) -> Padding:
    grid = _grid()
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    for label, detail in pairs:
        # A pinned image ref runs to ~150 characters; doctor's own render
        # shortens its digest the same way.
        grid.add_row(f"{label}:", Text(_short_digest(detail)))
    return Padding(grid, (0, 0, 0, 2))


def summary_rich(s: Summary) -> RenderableType:
    """The final report on a terminal: what's ready, what still fails, what's
    left for the person, and the next command."""
    parts: list[RenderableType] = []
    if not s.not_ready:
        tail = " Nothing to do." if s.nothing_to_do else ""
        parts.append(Text.from_markup(f"{_GLYPH['ok']} ready to {' · '.join(s.ready)}.{tail}"))
    else:
        if s.ready:
            parts.append(Text.from_markup(f"{_GLYPH['ok']} ready to {' · '.join(s.ready)}"))
        parts.append(Text.from_markup(f"{_GLYPH['fail']} not ready to {' · '.join(s.not_ready)}"))
        if s.failing:
            parts.append(_failing(s.failing))
    if s.yours:
        parts += [Text("Still to do (nethackers never runs sudo):"),
                  _bullets(escape(t.say) for t in s.yours)]
    if s.afterwards:
        parts += [Text("Afterwards:"), _bullets(escape(t.say) for t in s.afterwards)]
    if s.not_ready or s.yours:
        parts.append(Text("Then run `nethackers setup` again. It picks up where it left off."))
    if s.next_command:
        parts.append(Text(f"Next: {s.next_command}"))
    return Group(*parts)


def summary_plain(s: Summary) -> str:
    lines: list[str] = []
    if not s.not_ready:
        tail = " Nothing to do." if s.nothing_to_do else ""
        lines.append(f"{_WORD['ok']} ready to {' · '.join(s.ready)}.{tail}")
    else:
        if s.ready:
            lines.append(f"{_WORD['ok']} ready to {' · '.join(s.ready)}")
        lines.append(f"{_WORD['fail']} not ready to {' · '.join(s.not_ready)}")
        lines += [f"  {label}: {_short_digest(detail)}" for label, detail in s.failing]
    if s.yours:
        lines.append("Still to do (nethackers never runs sudo):")
        lines += [f"  • {t.say}" for t in s.yours]
    if s.afterwards:
        lines.append("Afterwards:")
        lines += [f"  • {t.say}" for t in s.afterwards]
    if s.not_ready or s.yours:
        lines.append("Then run `nethackers setup` again. It picks up where it left off.")
    if s.next_command:
        lines.append(f"Next: {s.next_command}")
    return "\n".join(lines)
