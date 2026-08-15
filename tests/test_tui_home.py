"""Tests for the Home dashboard (Task 11): the four pure panel formatters
(``your_solutions_panel``/``leaderboard_panel``/``recent_runs_panel``/
``attainment_panel``) plus a widget smoke test for ``HomeView``.

The pure panels are the tested deliverable -- each assertion below pins a
specific, easy-to-get-wrong behavior (which row the "you" marker lands on,
identity title-casing, the win-fraction substring, which milestone wins as
"deepest") rather than a weak "renders something" check. ``HomeView``
itself only gets a smoke test: it's exercised against a real (but
instantly connection-refused) loopback URL so ``_refresh``'s try/except
around each hub call is proven to degrade to a friendly message instead of
crashing the app -- no network mocking needed, since a closed local port
fails immediately.
"""
from __future__ import annotations

import io

from rich.console import Console
from textual.app import App, ComposeResult

from nethackers.tui.screens.home import (
    HomeView,
    _your_solutions,
    attainment_panel,
    leaderboard_panel,
    recent_runs_panel,
    your_solutions_panel,
)

# An address nothing listens on: httpx.ConnectError fires near-instantly
# (loopback, no DNS), so the smoke test never depends on real network access.
_DEAD_HUB = "http://127.0.0.1:1"


def _p(renderable) -> str:
    console = Console(width=90, file=io.StringIO())
    console.print(renderable)
    return console.file.getvalue()


# --- leaderboard_panel: owner-keyed, "you" highlight on the right row -----


def test_leaderboard_panel_marks_only_your_row():
    entries = [
        {"rank": 1, "owner": "vale", "mean_progression": 0.51},
        {"rank": 2, "owner": "castiel", "mean_progression": 0.44},
    ]
    out = _p(leaderboard_panel(entries, you="castiel"))
    assert "castiel ◀ you" in out
    assert "vale ◀ you" not in out  # marker must not bleed onto another owner's row


# --- your_solutions_panel: identity-keyed, title-cased, no owner column ---


def test_your_solutions_panel_title_cases_identity_and_has_no_owner_marker():
    rows = [{"identity": "wiz-elf-cha-mal", "score": 0.44, "solution_digest": "a" * 8}]
    out = _p(your_solutions_panel(rows))
    assert "Wiz-Elf-Cha-Mal" in out
    assert "@" not in out  # identity-keyed rows never render an "@owner" cell


# --- recent_runs_panel: objective + a distinguishable win/iteration ratio -


def test_recent_runs_panel_shows_objective_and_win_fraction():
    runs = [
        {"run_id": "r-1", "objective": "wiz-elf-cha-mal", "operator": "claude",
         "wins": 2, "iterations": 5, "best_dev": 0.44, "best_held": 0.41},
    ]
    out = _p(recent_runs_panel(runs))
    assert "wiz-elf-cha-mal" in out
    assert "2/5" in out  # wins/iterations, not just a lone digit that could match anything


# --- attainment_panel: deepest milestone wins, bar is a real partial gauge


def test_attainment_panel_reports_deepest_milestone_with_partial_bar():
    cells = [{"milestone": "Dlvl:12"}, {"milestone": "Dlvl:26"}]
    out = attainment_panel(cells, "wiz-elf-cha-mal")
    assert "Dlvl:26" in out       # the deeper of the two cells wins
    assert "Dlvl:12" not in out   # the shallower one must not be reported as "best"
    assert "▓" in out and "░" in out  # a partial gauge -- not all-filled, not all-empty


# --- _your_solutions: the search-digests x global-elites join -------------
#
# Real /search rows carry no identity/score (just registration metadata --
# see home.py's module docstring), so "your solutions" has to be joined
# against a global elites spread by digest. This pins that join: only a
# digest the caller actually owns survives, others (even a higher-scoring
# one) are filtered out.


class _FakeClient:
    """Duck-types HubClient's search()/elites() -- no real HubClient or
    network involved."""

    def __init__(self, search_rows, elite_rows):
        self._search_rows = search_rows
        self._elite_rows = elite_rows
        self.elites_calls = 0

    def search(self, owner):
        return self._search_rows

    def elites(self, objective):
        assert objective == "all"
        self.elites_calls += 1
        return self._elite_rows


def test_your_solutions_joins_owned_digests_against_global_elites():
    client = _FakeClient(
        search_rows=[{"digest": "sha256:mine", "owner": "castiel"}],
        elite_rows=[
            {"identity": "wiz-elf-cha-mal", "solution_digest": "sha256:mine",
             "score": 0.44, "rank": 1},
            {"identity": "val-dwa-law-fem", "solution_digest": "sha256:not-mine",
             "score": 0.90, "rank": 1},
        ],
    )
    yours = _your_solutions(client, "castiel")
    assert yours == [
        {"identity": "wiz-elf-cha-mal", "solution_digest": "sha256:mine",
         "score": 0.44, "rank": 1}
    ]


def test_your_solutions_skips_elites_call_when_nothing_registered():
    client = _FakeClient(search_rows=[], elite_rows=[{"should": "never be reached"}])
    assert _your_solutions(client, "castiel") == []
    assert client.elites_calls == 0  # no digests to join -- the 2nd hub call is skipped


# --- HomeView: widget smoke test -------------------------------------------


class _Host(App):
    def __init__(self, login: str | None):
        super().__init__()
        self._login = login

    def compose(self) -> ComposeResult:
        yield HomeView(_DEAD_HUB, self._login, id="home")


async def test_home_view_survives_unreachable_hub_and_renders_friendly_messages():
    host = _Host("tester")
    async with host.run_test() as pilot:
        await pilot.pause()
        board = str(host.query_one("#home_board").render())
        yours = str(host.query_one("#home_yours").render())
        attain = str(host.query_one("#home_attain").render())
        assert "unreachable" in board
        assert "unreachable" in yours
        assert "your attainment" in attain  # attainment falls back, doesn't crash


async def test_home_view_with_no_login_skips_search_but_still_renders():
    host = _Host(None)
    async with host.run_test() as pilot:
        await pilot.pause()
        yours = str(host.query_one("#home_yours").render())
        # No login -> no search call attempted -> no "yours" data, no crash either.
        assert "unreachable" not in yours
