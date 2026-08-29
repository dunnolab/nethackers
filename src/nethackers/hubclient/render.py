"""``rich`` renderers for the M2a hub-facing CLI read subcommands (CLI-UX
pass): ``render_board``/``render_elites``/``render_search``/
``render_show``, one per ``nethackers.cli`` read subcommand, each a pure
formatter over the JSON a ``HubClient`` read call returns -- same contract
as the baseline pure-Python renderers in ``nethackers.hubclient.client``
(which these sit alongside as the ``table`` half of
``hubclient.output.emit``'s ``table=``/``plain=`` pair; the baseline ones
become ``plain=``). Reuses that module's ``_short_digest``/``_num`` helpers
rather than duplicating them.

Every renderer must tolerate an empty/short response without crashing,
returning a friendly one-line ``rich.text.Text`` instead of a bare table
header -- the exact same message text the baseline ``plain`` renderers use,
so ``-o table`` and ``-o plain`` agree on empty input.

``render_frontier_grid`` is the one deliberate exception to that last rule:
it's a fixed-shape role x variation grid over all 73 ``IDENTITIES``
(shared by the TUI Frontier view and the CLI ``frontier`` command, not tied
to one ``nethackers.cli`` subcommand), so an empty/all-``None`` ``scores``
map still renders the full 13-role grid -- every cell just shows ``"—"``
rather than collapsing to a one-line message, since "no cell evaluated yet"
is itself the frontier's normal starting state, not an error/empty
condition to special-case away.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from rich import box
from rich.console import Group, JustifyMethod, RenderableType
from rich.table import Table
from rich.text import Text

from nethackers.hub.objectives import IDENTITIES
from nethackers.hubclient.client import _num, _short_digest

# Column (name, justify) specs for render_board's three known shapes --
# module-level and explicitly typed against rich's JustifyMethod Literal so
# mypy checks each "left"/"right" literal once here, rather than at every
# add_column call (a plain inline tuple-of-tuples infers as tuple[str, str],
# which add_column's Literal-typed `justify` parameter then rejects).
_GRADING_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"),
    ("program", "left"),
    ("owner", "left"),
    ("asc", "right"),
    ("median", "right"),
    ("mean", "right"),
)
_COVERAGE_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"), ("program", "left"), ("owner", "left"), ("cells", "right"),
)
_FIRSTS_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("rank", "right"), ("program", "left"), ("owner", "left"), ("firsts", "right"),
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


def _gh_user(login: str, you: str | None = None) -> Text:
    """A GitHub username as an OSC-8 terminal hyperlink to the profile. Owners
    are GitHub logins (register resolves the auth token -> login and requires
    the repo owner == login), so ``github.com/<login>`` is always the right
    target. Terminals without hyperlink support just show the plain name; the
    ``-o json`` path is untouched (agents get the plain login)."""
    login = str(login)
    if not login:
        return Text("")
    style = "link https://github.com/" + login
    if you is not None and login == you:
        style += " bold reverse"
    label = f"@ {login}" + (" ◀ you" if you is not None and login == you else "")
    return Text(label, style=style)


def _gh_repo(repo: str) -> Text:
    """A repo reference (e.g. ``github.com/owner/name``) as a clickable link."""
    repo = str(repo)
    if not repo:
        return Text("")
    url = repo if repo.startswith(("http://", "https://")) else f"https://{repo}"
    return Text(repo, style=f"link {url}")


def _gh_commit(repo: str, sha: str) -> Text:
    """A commit sha (shown short via ``_short_digest``) as a link to its GitHub
    commit page ``<repo>/commit/<full-sha>``. Needs the repo for the URL; falls
    back to plain short text if either the repo or the sha is missing. (The
    solution *digest* is NOT linked -- it's a content hash, not a git object.)"""
    sha, repo = str(sha), str(repo)
    short = _short_digest(sha)
    if not sha or not repo:
        return Text(short)
    base = repo if repo.startswith(("http://", "https://")) else f"https://{repo}"
    return Text(short, style=f"link {base}/commit/{sha}")


def render_board(entries: list[dict[str, Any]], *, you: str | None = None) -> RenderableType:
    """A ``rich`` table of board entries, shape-aware over which metric
    produced them (mirrors the baseline ``plain`` ``render_board``'s shape
    detection exactly, so ``-o table``/``-o plain`` never disagree on
    which columns a given response gets):

    - grading board (``/board``'s one row shape -- entries carry
      ``ascensions``): ``rank | program | owner | asc | median | mean``,
      ``median``/``mean`` colored via ``ramp``.
    - coverage board (``/achievements/coverage`` -- entries carry
      ``cells_held``): ``rank | program | owner | cells``.
    - firsts board (``/achievements/firsts`` -- entries carry ``firsts``):
      ``rank | program | owner | firsts``.
    - unknown shape: falls back to the first entry's own (sorted) keys.

    Numeric columns right-aligned, ``program`` the opaque ``program_id``
    shown verbatim (already short -- no ``_short_digest`` truncation),
    bold header, faint zebra striping. Empty -> a friendly one-line
    message, never a bare header."""
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
                str(e.get("program_id", "")),
                _gh_user(e.get("owner", ""), you=you),
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
                str(e.get("program_id", "")),
                _gh_user(e.get("owner", ""), you=you),
                str(e.get("cells_held", "")),
            )
    elif "firsts" in first:
        for name, justify in _FIRSTS_COLUMNS:
            table.add_column(name, justify=justify)
        for e in entries:
            table.add_row(
                str(e.get("rank", "")),
                str(e.get("program_id", "")),
                _gh_user(e.get("owner", ""), you=you),
                str(e.get("firsts", "")),
            )
    else:
        headers = sorted(first)
        for name in headers:
            table.add_column(name)
        for e in entries:
            table.add_row(*[str(e.get(h, "")) for h in headers])

    return table


def render_elites(entries: list[dict[str, Any]]) -> RenderableType:
    """A ``rich`` table of elite entries: ``rank | identity | program |
    score`` -- ``program`` the opaque ``program_id`` shown verbatim
    (already short -- no ``_short_digest`` truncation), ``score`` colored
    via ``ramp``. Empty -> a friendly one-line message, never a bare
    header."""
    if not entries:
        return _empty("no elites recorded yet.")

    table = Table(header_style="bold", row_styles=["", "on grey11"])
    table.add_column("rank", justify="right", no_wrap=True)
    table.add_column("identity", no_wrap=True)
    table.add_column("program", overflow="fold", no_wrap=False)
    table.add_column("score", justify="right", no_wrap=True)
    for e in entries:
        table.add_row(
            str(e.get("rank", "")),
            str(e.get("identity", "")),
            str(e.get("program_id", "")),
            _colored_num(e.get("score", "")),
        )
    return table


# Role code -> full display name (all 13 NetHack roles), and the frontier
# grid's fixed role display order -- ``list(ROLE_FULL)`` walks its
# insertion order, which is alphabetical by role code, matching
# ``IDENTITIES``' own sort so a role's header and its variations always
# agree on grouping.
ROLE_FULL: dict[str, str] = {
    "arc": "Archeologist", "bar": "Barbarian", "cav": "Caveman", "hea": "Healer",
    "kni": "Knight", "mon": "Monk", "pri": "Priest", "ran": "Ranger",
    "rog": "Rogue", "sam": "Samurai", "tou": "Tourist", "val": "Valkyrie",
    "wiz": "Wizard",
}
ROLE_ORDER: list[str] = list(ROLE_FULL)


def _frontier_numstyle(v: float | None) -> str:
    """The tint for one frontier-grid cell's progression number -- a scan
    aid, not a verdict (a handful of discrete bands, unlike ``ramp``'s
    continuous heat gradient): dim grey for an unevaluated (``None``)
    cell; green tones for any evaluated value below the near-ascension
    cutoffs, most saturated just above zero and paling through three bands
    up to ``0.65``; amber from ``0.65`` up to ``0.8746``; bold gold at/above
    ``0.8746`` (``ACHIEVEMENTS["Astral Plane"]`` rounded to 4dp -- the
    milestone one step short of full ascension at ``1.0``)."""
    if v is None:
        return "#5a5a52"
    if v >= 0.8746:
        return "bold #ffd54a"
    if v < 0.10:
        return "#3f9e7f"
    if v < 0.25:
        return "#57c99a"
    if v < 0.45:
        return "#9be3b8"
    if v < 0.65:
        return "#c7f0d8"
    return "#d2a24c"


def _mean(values: list[float]) -> float | None:
    """The arithmetic mean of ``values``, or ``None`` for an empty list --
    the frontier grid's ONLY aggregate (never max/best): a role's header
    number always answers "how is this role doing on average", never
    "what's its single best result so far"."""
    return sum(values) / len(values) if values else None


def _frontier_pct(value: float) -> str:
    """Contest-site frontier formatting: progression as a percentage."""
    return f"{value * 100:.1f}%"


def _frontier_delta(value: float) -> str:
    """Contest-site delta formatting: signed percentage points."""
    return f"{value * 100:+.1f}%"


def render_frontier_grid(
    scores: dict[str, float | None], *, note: str = "",
    baseline_scores: Mapping[str, float | None] | None = None,
    baseline_floor: bool = False,
) -> RenderableType:
    """The frontier ``{identity: value}`` map as a 3-column grid, one cell
    per role (all 13, in ``ROLE_ORDER``): the role's full name, its mean
    progression across evaluated variations (or ``"—"`` if
    none evaluated -- the aggregate is always a MEAN, never a max/best, see
    ``_mean``), then one line per variation -- its ``race-align-gender``
    label and its progression number, tinted via ``_frontier_numstyle`` as
    a scan aid only (the number is the reading; the color just helps a
    glance find the interesting cells). A variation with no value yet
    (absent from ``scores``, or present as ``None``) renders ``"—"``
    rather than a blank, so "not evaluated" always reads as a deliberate
    dash, never empty space that could pass for a layout gap.

    Always draws all 73 ``IDENTITIES`` grouped by role -- ``scores`` need
    not be complete, and ``render_frontier_grid({})`` still renders the
    full grid (every cell ``"—"``); see the module docstring for why this
    renderer, alone, doesn't collapse empty input down to a one-line
    message the way this module's other renderers do.

    When ``baseline_scores`` is supplied, each evaluated program value also
    shows its signed delta versus AutoAscend. With ``baseline_floor=True``,
    AutoAscend participates in the Universe: a missing or non-beating program
    cell displays the AA score followed by ``aa``, matching the contest-site
    frontier table instead of falsely presenting that identity as unexplored.

    An optional ``note`` (e.g. ``"frozen -- parent gen 4"``) renders as a
    dim line above the grid via ``rich.console.Group``, for callers that
    want to caption the grid without the caption becoming part of the
    table itself."""
    by_role: dict[str, list[str]] = defaultdict(list)
    for ident in IDENTITIES:
        by_role[ident.split("-")[0]].append(ident)

    cells: list[Text] = []
    for role in ROLE_ORDER:
        idents = sorted(by_role.get(role, []))
        displayed: dict[str, float | None] = {}
        for ident in idents:
            value = scores.get(ident)
            aa = baseline_scores.get(ident) if baseline_scores is not None else None
            if baseline_floor and aa is not None and (value is None or value <= aa + 0.0005):
                value = aa
            displayed[ident] = value
        present = [displayed[i] for i in idents if displayed[i] is not None]
        agg = _mean([v for v in present if v is not None])
        head = Text(ROLE_FULL[role], style="bold #d2a24c")
        if baseline_scores is None:
            head.append(f"   mean {agg:.2f}\n" if agg is not None else "   —\n", style="dim")
        elif agg is None:
            head.append("   —\n", style="dim")
        else:
            head.append(f"   {_frontier_pct(agg)}", style="bold")
            role_deltas: list[float] = []
            program_leads = False
            for ident in idents:
                shown_value = displayed[ident]
                aa_value = baseline_scores.get(ident)
                raw_value = scores.get(ident)
                if shown_value is not None and aa_value is not None:
                    role_deltas.append(shown_value - aa_value)
                if (raw_value is not None
                        and (aa_value is None or raw_value > aa_value + 0.0005)):
                    program_leads = True
            role_delta = _mean(role_deltas)
            if baseline_floor and not program_leads:
                head.append("  aa\n", style="dim #9a9aa6")
            elif role_delta is not None:
                delta_style = "#57c99a" if role_delta > 0.0005 else (
                    "#d97979" if role_delta < -0.0005 else "dim")
                head.append(f" {_frontier_delta(role_delta)}\n", style=delta_style)
            else:
                head.append("   —\n", style="dim")
        for ident in idents:
            raw = scores.get(ident)
            aa = baseline_scores.get(ident) if baseline_scores is not None else None
            v = displayed[ident]
            head.append(f"{ident.split('-', 1)[1]:<12} ", style="#8a8069")
            shown = (_frontier_pct(v) if baseline_scores is not None
                     else f"{v:>4.2f}") if v is not None else "  — "
            head.append(shown,
                        style=_frontier_numstyle(v))
            if baseline_scores is not None:
                is_floor = (baseline_floor and aa is not None
                            and (raw is None or raw <= aa + 0.0005))
                if is_floor:
                    head.append("  aa", style="dim #9a9aa6")
                elif raw is not None and aa is not None:
                    delta = raw - aa
                    delta_style = "#57c99a" if delta > 0.0005 else (
                        "#d97979" if delta < -0.0005 else "dim")
                    head.append(f" {_frontier_delta(delta)}", style=delta_style)
                else:
                    head.append("   —", style="dim")
            head.append("\n")
        cells.append(head)

    # Three cards fit a conventional 80-column terminal while leaving room
    # for the contest table's AA/delta column. Four made even the old score-
    # only rows wrap; with comparison data they became effectively unreadable.
    columns = 3
    table = Table(box=box.SQUARE, show_header=False, pad_edge=False,
                  padding=(0, 1), border_style="#4a4436", expand=True)
    for _ in range(columns):
        table.add_column(ratio=1)
    for i in range(0, len(cells), columns):
        row = cells[i:i + columns]
        row += [Text("")] * (columns - len(row))
        table.add_row(*row)

    if note:
        return Group(Text(note, style="dim"), Text(""), table)
    return table


def render_search(results: list[dict[str, Any]]) -> RenderableType:
    """A ``rich`` table of registered programs: ``program | owner | repo |
    commit | registered`` -- ``program`` the opaque ``id`` shown verbatim
    (already short -- no ``_short_digest`` truncation), ``reference``
    flattened into linked ``repo``/``commit`` cells (``commit`` still
    shortened via ``_short_digest``, a real git object unlike the retired
    content-hash digest). Empty -> a friendly one-line message, never a
    bare header."""
    if not results:
        return _empty("no solutions found.")

    table = Table(header_style="bold", row_styles=["", "on grey11"])
    for name in ("program", "owner", "repo", "commit", "registered"):
        table.add_column(name)
    for r in results:
        reference = r.get("reference") or {}
        repo = str(reference.get("repo", ""))
        table.add_row(
            str(r.get("id", "")),
            _gh_user(r.get("owner", "")),
            _gh_repo(repo),
            _gh_commit(repo, str(reference.get("commit", ""))),
            str(r.get("registered_at", "")),
        )
    return table


def render_show(program: dict[str, Any]) -> RenderableType:
    """A 2-column key/value grid describing one registered program (the
    ``/programs/{id}`` object: ``{id, owner, reference:{repo,commit},
    registered_at}``): ``id`` shown verbatim (opaque, already short --
    no ``_short_digest`` truncation), ``reference`` flattened into linked
    ``repo``/``commit`` rows via ``_gh_repo``/``_gh_commit`` (``commit``
    still shortened -- a real git object, unlike the retired digest),
    ``owner`` linked via ``_gh_user``. Empty/missing -> a friendly one-line
    message, never an empty block."""
    if not program:
        return _empty("no such solution.")

    reference = program.get("reference") or {}
    repo = str(reference.get("repo", ""))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="bold")
    grid.add_column()
    grid.add_row("id", str(program.get("id", "")))
    grid.add_row("repo", _gh_repo(repo))
    grid.add_row("commit", _gh_commit(repo, str(reference.get("commit", ""))))
    grid.add_row("owner", _gh_user(str(program.get("owner", ""))))
    grid.add_row("registered_at", str(program.get("registered_at", "")))
    return grid
