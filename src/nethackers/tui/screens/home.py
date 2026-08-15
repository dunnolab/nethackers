"""Textual Home view (Task 11): the dashboard landing screen -- your
solutions, the public leaderboard, recent local evolve runs, and milestone
attainment, over four pure panel formatters plus the ``HomeView`` widget
that fetches hub data and local run logs on mount/show and renders them.

Real ``HubClient`` shapes (verified against ``hub/api.py``/``hub/views/*``,
not assumed): ``board()``/``search()``/``attainment()``/``elites()`` all
return **bare lists** of dicts -- every ``/board``, ``/search``,
``/attainment``, and ``/elites`` route handler in ``hub/api.py`` is typed
``-> list[dict[str, Any]]`` and FastAPI serializes that straight to a JSON
array, no ``{"entries": [...]}``/``{"data": ...}`` envelope anywhere. So no
unwrapping is needed when wiring ``HomeView._refresh``.

But ``search()``'s rows are registration metadata only (``digest, repo,
commit_sha, owner, root, entrypoint, registered_at`` -- see
``hub/store.py``'s ``_SOLUTION_COLUMNS``/``hub/api.py``'s
``_SEARCH_COLUMNS``): they carry no ``identity``/``score`` at all, unlike
this module's own ``your_solutions_panel`` contract (identity-keyed, fed by
rows that DO carry ``identity``/``score``/``solution_digest`` --
``elites()``'s shape per ``hub/views/elites.py``). Feeding raw ``search()``
rows straight into ``your_solutions_panel`` would silently render owner-
keyed junk (every row "@<login>", score 0.000, Dlvl:1) instead of the
intended identity-keyed high-score view. ``_your_solutions`` below resolves
this the same way the hub's own data model does: the caller's own solution
digests from ``search(owner=login)``, joined against a global elites spread
(``elites("all")``, which the hub keeps fresh -- ``hub/validate.py``'s
``register()`` calls ``recompute_elites`` on every registration, exactly
like ``hub/fixtures.py`` mirrors for its demo dataset) filtered down to
just those digests. Both calls are guarded by one try/except in
``_refresh`` so a hub outage renders a friendly "hub unreachable" message
instead of a crash -- or, worse, an empty-but-silent panel indistinguishable
from "you haven't registered anything yet".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.hubclient.client import HubClient
from nethackers.tui.art import highscore_table, score_to_dlvl
from nethackers.tui.screens.runs import read_runs

_RUNS_DIR = Path.home() / ".nethackers" / "evolve" / "runs"
_DLVL_BAR_CELLS = 12


def your_solutions_panel(entries: list[dict[str, Any]]) -> Table:
    """Your own registered solutions, identity-keyed (rows carry no
    ``owner`` -- every row here is already yours, so ``highscore_table``
    takes its identity-keyed branch: a title-cased identity instead of an
    ``@owner`` cell)."""
    return highscore_table(entries)


def leaderboard_panel(entries: list[dict[str, Any]], you: str | None) -> Table:
    """The public ranking board: owner-keyed, with ``you``'s row bolded and
    marked ``◀ you``."""
    return highscore_table(entries, you=you)


def recent_runs_panel(runs: list[dict[str, Any]]) -> Table:
    """The most recent local evolve runs (``read_runs``'s per-run
    summaries): run id, objective, operator, a ``wins/iterations``
    fraction, and best dev/held scores. Capped to the 6 most recent --
    ``read_runs`` already sorts newest-first."""
    t = Table(header_style="bold", pad_edge=False)
    for c in ("run", "objective", "op", "wins", "dev", "held"):
        t.add_column(c)
    for r in runs[:6]:
        t.add_row(
            str(r["run_id"]), str(r["objective"]), str(r.get("operator", "")),
            f"{r['wins']}/{r['iterations']}",
            f"{(r.get('best_dev') or 0):.2f}", f"{(r.get('best_held') or 0):.2f}",
        )
    return t


def attainment_panel(cells: list[dict[str, Any]], identity: str) -> str:
    """A one-line Dlvl depth gauge for ``identity``: the deepest milestone
    lit across ``cells`` (``read_attainment``'s rows, via
    ``ACHIEVEMENTS``), rendered as a 12-cell filled/empty bar plus the
    milestone label itself."""
    deepest = max(
        (ACHIEVEMENTS.get(str(c.get("milestone", "")), 0.0) for c in cells), default=0.0
    )
    label = score_to_dlvl(deepest)
    filled = round(deepest * _DLVL_BAR_CELLS)
    bar = "▓" * filled + "░" * (_DLVL_BAR_CELLS - filled)
    return f"your attainment · {identity}\nDlvl {bar}  best {label}"


def _your_solutions(client: HubClient, login: str) -> list[dict[str, Any]]:
    """Identity-keyed ``{identity, solution_digest, score, rank}`` entries
    for solutions ``login`` owns (see module docstring for why this is a
    join, not a single call). May raise -- ``HomeView._refresh`` wraps the
    call in the try/except that turns a hub outage into a friendly
    message, same as it does for the board/attainment panels."""
    mine = client.search(login) or []
    digests = {str(row.get("digest", "")) for row in mine}
    if not digests:
        return []
    elite = client.elites("all") or []
    return [e for e in elite if str(e.get("solution_digest", "")) in digests]


class HomeView(Vertical):
    """The dashboard landing view: your solutions, the leaderboard, recent
    runs, and attainment, in a 2x2 grid of panels. Fetched fresh on mount
    and every time this view is shown again (``on_show``) -- each hub-
    backed panel is refreshed independently so one failing call can't blank
    out the other three."""

    def __init__(self, hub: str, login: str | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._login = login

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(id="home_yours")
            yield Static(id="home_board")
        with Horizontal():
            yield Static(id="home_runs")
            yield Static(id="home_attain")

    def on_mount(self) -> None:
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        client = HubClient(self._hub)
        unreachable = f"hub unreachable: {self._hub}"

        try:
            board_entries = client.board("random") or []
        except Exception:
            self.query_one("#home_board", Static).update(unreachable)
        else:
            self.query_one("#home_board", Static).update(
                leaderboard_panel(board_entries, self._login)
            )

        yours: list[dict[str, Any]] = []
        try:
            if self._login:
                yours = _your_solutions(client, self._login)
        except Exception:
            self.query_one("#home_yours", Static).update(unreachable)
        else:
            self.query_one("#home_yours", Static).update(your_solutions_panel(yours))

        self.query_one("#home_runs", Static).update(recent_runs_panel(read_runs(_RUNS_DIR)))

        # Attainment for the user's best identity (first join hit), if any.
        identity = str(yours[0].get("identity", "")) if yours else ""
        try:
            cells = client.attainment(identity) if identity else []
        except Exception:
            self.query_one("#home_attain", Static).update(unreachable)
        else:
            self.query_one("#home_attain", Static).update(
                attainment_panel(cells or [], identity or "—")
            )
