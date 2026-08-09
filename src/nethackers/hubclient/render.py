"""``rich`` renderers for the M2a hub-facing CLI read subcommands (CLI-UX
pass): ``render_board``/``render_attainment``/``render_elites``/
``render_search``/``render_show``, one per ``nethackers.cli`` read
subcommand, each a pure formatter over the JSON a ``HubClient`` read call
returns -- same contract as the baseline pure-Python renderers in
``nethackers.hubclient.client`` (which these sit alongside as the ``table``
half of ``hubclient.output.emit``'s ``table=``/``plain=`` pair; the
baseline ones become ``plain=``). Reuses that module's ``_short_digest``/
``_num`` helpers rather than duplicating them.

``render_attainment`` is the showcase: the attainment MAP as a colored
heatmap grid rather than a flat table -- see its docstring.

Every renderer must tolerate an empty/short response without crashing,
returning a friendly one-line ``rich.text.Text`` instead of a bare table
header -- the exact same message text the baseline ``plain`` renderers use,
so ``-o table`` and ``-o plain`` agree on empty input.
"""

from __future__ import annotations

from typing import Any

from rich.console import JustifyMethod, RenderableType
from rich.table import Table
from rich.text import Text

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.hubclient.client import _num, _short_digest

# Column (name, justify) specs for render_board's three known shapes --
# module-level and explicitly typed against rich's JustifyMethod Literal so
# mypy checks each "left"/"right" literal once here, rather than at every
# add_column call (a plain inline tuple-of-tuples infers as tuple[str, str],
# which add_column's Literal-typed `justify` parameter then rejects).
_GRADING_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"),
    ("solution", "left"),
    ("owner", "left"),
    ("asc", "right"),
    ("median", "right"),
    ("mean", "right"),
)
_COVERAGE_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"), ("solution", "left"), ("owner", "left"), ("cells", "right"),
)
_FIRSTS_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"), ("solution", "left"), ("owner", "left"), ("firsts", "right"),
)

# A 5-stop viridis-ish gradient (dark purple -> teal -> yellow): low values
# read as "cold"/sparse, high values as "hot"/dense -- used both for
# progression scores (asc/median/mean, elite score) and for the
# attainment map's holder-density coloring.
_RAMP: tuple[tuple[int, int, int], ...] = (
    (68, 1, 84),
    (59, 82, 139),
    (33, 145, 140),
    (94, 201, 98),
    (253, 231, 37),
)


def ramp(x: float) -> str:
    """Map ``x`` in ``[0, 1]`` to an ``"rgb(r,g,b)"`` rich color string, by
    linearly interpolating between the two nearest stops of ``_RAMP``.
    Clamped to ``[0, 1]`` first, so any out-of-range input (including
    already-capped holder-density ratios) never raises or indexes out of
    bounds."""
    position = min(max(float(x), 0.0), 1.0) * (len(_RAMP) - 1)
    i = int(position)
    frac = position - i
    a, b = _RAMP[i], _RAMP[min(i + 1, len(_RAMP) - 1)]
    r, g, bl = (int(a[k] + frac * (b[k] - a[k])) for k in range(3))
    return f"rgb({r},{g},{bl})"


def _colored_num(value: Any, nd: int = 3) -> Text:
    """``_num(value)`` as a ``Text`` colored via ``ramp`` when ``value``
    parses as a float in a sane 0..1-ish progress/score range; plain,
    unstyled text otherwise (e.g. ``""``/``None`` from a missing key) --
    coloring is a bonus, never a crash risk."""
    rendered = _num(value, nd)
    try:
        color = ramp(float(value))
    except (TypeError, ValueError):
        return Text(rendered)
    return Text(rendered, style=color)


def _empty(message: str) -> Text:
    return Text(message, style="italic dim")


def render_board(entries: list[dict[str, Any]]) -> RenderableType:
    """A ``rich`` table of board entries, shape-aware over which metric
    produced them (mirrors the baseline ``plain`` ``render_board``'s shape
    detection exactly, so ``-o table``/``-o plain`` never disagree on
    which columns a given response gets):

    - grading board (entries carry ``ascensions``): ``rank | solution |
      owner | asc | median | mean``, ``median``/``mean`` colored via
      ``ramp``.
    - coverage board (entries carry ``cells_held``): ``rank | solution |
      owner | cells``.
    - firsts board (entries carry ``firsts``): ``rank | solution | owner |
      firsts``.
    - unknown shape: falls back to the first entry's own (sorted) keys.

    Numeric columns right-aligned, ``solution`` shortened via
    ``_short_digest``, bold header, faint zebra striping. Empty -> a
    friendly one-line message, never a bare header."""
    if not entries:
        return _empty("no board entries yet.")

    first = entries[0]
    table = Table(header_style="bold", row_styles=["", "on grey11"])

    if "ascensions" in first:
        for name, justify in _GRADING_COLUMNS:
            table.add_column(name, justify=justify)
        for e in entries:
            table.add_row(
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
                str(e.get("owner", "")),
                str(e.get("ascensions", "")),
                _colored_num(e.get("median_progression", "")),
                _colored_num(e.get("mean_progression", "")),
            )
    elif "cells_held" in first:
        for name, justify in _COVERAGE_COLUMNS:
            table.add_column(name, justify=justify)
        for e in entries:
            table.add_row(
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
                str(e.get("owner", "")),
                str(e.get("cells_held", "")),
            )
    elif "firsts" in first:
        for name, justify in _FIRSTS_COLUMNS:
            table.add_column(name, justify=justify)
        for e in entries:
            table.add_row(
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
                str(e.get("owner", "")),
                str(e.get("firsts", "")),
            )
    else:
        headers = sorted(first)
        for name in headers:
            table.add_column(name)
        for e in entries:
            table.add_row(*[str(e.get(h, "")) for h in headers])

    return table


def render_attainment(cells: list[dict[str, Any]]) -> RenderableType:
    """The attainment MAP as a colored heatmap grid -- the showcase
    render. Pivots the flat cell list the hub returns (``[{identity,
    milestone, first_owner, holder_count}, ...]``) into rows = identities
    present in the response (sorted) x columns = milestones present
    (sorted easiest-first by ``nethackers.arena.progress.ACHIEVEMENTS``'s
    empirical-ascension-probability value, so the grid reads left-to-right
    as a difficulty ladder).

    Each cell is one character: a solid block (``"█"``) colored by holder
    density (``ramp(min(holder_count, 8) / 8)`` -- density saturates at 8+
    holders so one outlier can't wash out the whole gradient) if that
    identity/milestone was reached by anyone, a dim middle-dot (``"·"``) if
    not. Below the grid, a one-line color legend and a caption spelling out
    the exact milestone order (row labels are self-evident; a 1-character-wide
    column can't also carry its own name, so the caption is where a column
    index maps back to a milestone).

    This renders only the identities/milestones actually present in the
    response -- compact and data-driven, not a fixed 73-row grid. Empty
    input -> a friendly one-line message, never a bare/zero-size grid."""
    if not cells:
        return _empty("no attainment cells yet.")

    identities = sorted({str(c.get("identity", "")) for c in cells})
    milestones = sorted(
        {str(c.get("milestone", "")) for c in cells},
        key=lambda m: ACHIEVEMENTS.get(m, 0.0),
    )
    holders: dict[tuple[str, str], int] = {
        (str(c.get("identity", "")), str(c.get("milestone", ""))): int(c.get("holder_count") or 0)
        for c in cells
    }
    label_width = max(len(identity) for identity in identities)

    grid = Text(no_wrap=True, overflow="crop")
    for identity in identities:
        grid.append(identity.ljust(label_width) + "  ")
        for milestone in milestones:
            count = holders.get((identity, milestone))
            if count is None:
                grid.append("·", style="grey42")  # unreached: dim middle-dot
            else:
                grid.append("█", style=ramp(min(count, 8) / 8))  # reached: colored block
        grid.append("\n")

    grid.append("\n")
    grid.append("legend:  ")
    grid.append("·", style="grey42")
    grid.append(" unreached    reached: ")
    for n in (1, 2, 4, 8):
        grid.append("█", style=ramp(min(n, 8) / 8))
    grid.append(" (low→high holders)\n")
    grid.append(
        f"{len(identities)} identities x {len(milestones)} milestones: " + ", ".join(milestones),
        style="dim",
    )
    return grid


def render_elites(entries: list[dict[str, Any]]) -> RenderableType:
    """A ``rich`` table of elite-pool entries: ``rank | identity |
    solution | score`` (``solution`` shortened via ``_short_digest``,
    ``score`` colored via ``ramp``). Empty -> a friendly one-line message,
    never a bare header."""
    if not entries:
        return _empty("no elites recorded yet.")

    table = Table(header_style="bold", row_styles=["", "on grey11"])
    table.add_column("rank", justify="right")
    table.add_column("identity")
    table.add_column("solution")
    table.add_column("score", justify="right")
    for e in entries:
        table.add_row(
            str(e.get("rank", "")),
            str(e.get("identity", "")),
            _short_digest(str(e.get("solution_digest", ""))),
            _colored_num(e.get("score", "")),
        )
    return table


def render_search(results: list[dict[str, Any]]) -> RenderableType:
    """A ``rich`` table of registered solutions: ``solution | owner | repo
    | commit | registered`` (``solution``/``commit`` shortened via
    ``_short_digest``). Empty -> a friendly one-line message, never a bare
    header."""
    if not results:
        return _empty("no solutions found.")

    table = Table(header_style="bold", row_styles=["", "on grey11"])
    for name in ("solution", "owner", "repo", "commit", "registered"):
        table.add_column(name)
    for r in results:
        table.add_row(
            _short_digest(str(r.get("digest", ""))),
            str(r.get("owner", "")),
            str(r.get("repo", "")),
            _short_digest(str(r.get("commit_sha", ""))),
            str(r.get("registered_at", "")),
        )
    return table


def render_show(solution: dict[str, Any]) -> RenderableType:
    """A 2-column key/value grid describing one registered solution
    (``digest``/``commit_sha`` shortened via ``_short_digest``), the same
    preferred-key ordering as the baseline ``plain`` ``render_show``.
    Empty/missing -> a friendly one-line message, never an empty block."""
    if not solution:
        return _empty("no such solution.")

    preferred = ["digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at"]
    keys = [k for k in preferred if k in solution]
    keys += [k for k in solution if k not in preferred]

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="bold")
    grid.add_column()
    for key in keys:
        value = solution[key]
        if key in ("digest", "commit_sha"):
            value = _short_digest(str(value))
        grid.add_row(key, str(value))
    return grid
