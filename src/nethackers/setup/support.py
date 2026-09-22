"""What setup can do on each OS, and how sure we are that it works.

A ``Recipe`` is one OS-specific action: install Colima, start Docker Desktop,
print the Docker install lines for Debian. ``who`` says whether nethackers
runs it or prints it for the person; ``support`` says what evidence backs it:

- ``Tested``: someone ran ``nethackers setup`` end to end on a real machine.
  The record says where, at which nethackers version, and when.
- ``Untested``: built from the vendor document it links; never run on real
  hardware.
- ``NotCovered``: nethackers has no recipe here; setup says so and points at
  the vendor's own install page.

A recipe becomes ``Tested`` only in a PR that records where it ran -- nothing
promotes itself. docs/setup.md's support table is generated from these
records (``setup/docs.py``); tests/test_setup_recipes.py fails when the two
disagree. "Verified" is avoided on purpose: it names the hidden-seed scoring
tier.

Leaf module: stdlib only.
"""
from __future__ import annotations

import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

SETUP_CMD = "nethackers setup"


@dataclass(frozen=True)
class Tested:
    __test__ = False  # the name starts with "Test": keep pytest from collecting it
    on: str    # e.g. "macOS 15.5 arm64, Docker Desktop 4.92"
    at: str    # the nethackers version it ran at, e.g. "0.35.0"
    date: str  # ISO date, e.g. "2026-09-25"


@dataclass(frozen=True)
class Untested:
    built_from: str  # URL of the vendor document the recipe follows


@dataclass(frozen=True)
class NotCovered:
    link: str  # the vendor's own install page


Support = Tested | Untested | NotCovered


@dataclass(frozen=True)
class Recipe:
    """One OS-specific action. ``argv`` is what nethackers runs (``who ==
    "nethackers"``); ``say`` is the instruction printed for the person (``who
    == "you"``)."""

    id: str
    does: str
    who: Literal["nethackers", "you"]
    support: Support
    argv: tuple[str, ...] = ()
    say: str = ""

    def __post_init__(self) -> None:
        if self.who == "nethackers" and not self.argv:
            raise ValueError(f"{self.id}: a recipe nethackers runs needs argv")
        if self.who == "you" and not self.say:
            raise ValueError(f"{self.id}: a printed recipe needs say")


def shown_command(argv: Iterable[str]) -> str:
    """A command exactly as the plan shows it: the script of a ``sh -c``
    pipeline, anything else shell-quoted."""
    parts = tuple(argv)
    if len(parts) == 3 and parts[:2] == ("sh", "-c"):
        return parts[2]
    return shlex.join(parts)


def support_label(support: Support) -> str:
    """"tested", "untested" or "not covered"."""
    if isinstance(support, Tested):
        return "tested"
    if isinstance(support, Untested):
        return "untested"
    return "not covered"


def fix_text(recipes: Iterable[Recipe], *, then_setup: bool = True) -> str | None:
    """doctor's one fix line for a state these recipes address: run setup when
    it can do any of it itself; otherwise the printed instructions, followed
    (``then_setup``) by "then run `nethackers setup`" so the rest still gets
    done. ``None`` when there is nothing to do."""
    todo = tuple(recipes)
    if not todo:
        return None
    if any(r.who == "nethackers" for r in todo):
        return f"run `{SETUP_CMD}`"
    said = "; ".join(r.say for r in todo)
    return f"{said}; then run `{SETUP_CMD}`" if then_setup else said


def mb_text(n: int) -> str:
    """Bytes the way the plan shows them: decimal megabytes, like docker."""
    return f"{n / 1_000_000:.0f} MB"


def render_table(sections: Iterable[tuple[str, Iterable[Recipe]]]) -> str:
    """docs/setup.md's support table: one sub-section per OS, one row per
    recipe."""
    lines: list[str] = []
    for title, recipes in sections:
        lines += [f"### {title}", "",
                  "| Recipe | What it does | Runs it | Command or instruction | Status |",
                  "|---|---|---|---|---|"]
        for r in recipes:
            what = f"`{shown_command(r.argv)}`" if r.argv else r.say
            lines.append(f"| `{r.id}` | {r.does} | {r.who} | {_cell(what)} | "
                         f"{_status_cell(r.support)} |")
        lines.append("")
    return "\n".join(lines)


def _cell(text: str) -> str:
    # GitHub tables need pipes escaped even inside code spans.
    return text.replace("|", "\\|")


def _status_cell(support: Support) -> str:
    if isinstance(support, Tested):
        return f"tested on {support.on} (nethackers {support.at}, {support.date})"
    if isinstance(support, Untested):
        return f"untested ([built from]({support.built_from}))"
    return f"not covered ([vendor page]({support.link}))"
