"""Tests for tui.screens.hub view components."""

from __future__ import annotations

import io

from rich.console import Console
from textual.app import App, ComposeResult
from textual.widgets import Tabs

from nethackers.hubclient.credentials import Credentials
from nethackers.hubclient.frontier import overall_mean
from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView

# An address nothing listens on: httpx.ConnectError fires near-instantly
# (loopback, no DNS), so the smoke test never depends on real network access.
_DEAD_HUB = "http://127.0.0.1:1"


def _render_to_str(renderable) -> str:
    """Render a rich renderable to plain text for assertion."""
    string_file: io.StringIO = io.StringIO()
    # This helper is for structural text assertions, so do not emit terminal
    # styling. With force_terminal=True Rich may place an ANSI reset between
    # adjacent differently-styled spans (for example ``25.0%`` and ``aa``),
    # making a visually contiguous string fail a raw substring assertion. The
    # exact reset placement varies across Rich/Python environments.
    console = Console(width=80, file=string_file, force_terminal=False, color_system=None)
    console.print(renderable)
    return string_file.getvalue()


async def _settle(app, pilot) -> None:
    """Let the view's on_show fire, then wait for its worker-thread fetch to
    finish and its result to be applied -- the views now load off the UI thread
    (``@work(thread=True)``), so a bare ``pilot.pause()`` would sample the
    ``loading…`` placeholder, not the fetched content."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


class _HostBoards(App):
    """Test host app for mounting BoardsView."""

    def compose(self) -> ComposeResult:
        yield BoardsView(_DEAD_HUB, "castiel", id="boards")


class _HostMap(App):
    """Test host app for mounting MapView."""

    def compose(self) -> ComposeResult:
        yield MapView(_DEAD_HUB, "castiel", id="map")


class _HostElites(App):
    """Test host app for mounting ElitesView."""

    def compose(self) -> ComposeResult:
        yield ElitesView(_DEAD_HUB, "castiel", id="elites")


# --- Hub unreachable tests: verify friendly error messages, not crashes -----


async def test_boards_view_handles_hub_unreachable():
    """BoardsView renders friendly error on hub outage, not a crash."""
    app = _HostBoards()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#boards_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


async def test_map_view_handles_hub_unreachable():
    """MapView renders friendly error on hub outage, not a crash."""
    app = _HostMap()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#map_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


async def test_elites_view_handles_hub_unreachable():
    """ElitesView renders friendly error on hub outage, not a crash."""
    app = _HostElites()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#elites_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


# --- Non-blocking regression: the fetch must NOT run on the UI thread -------
#
# The lag bug: each view fetched from the hub synchronously inside on_mount/
# on_show, freezing the terminal for the whole round-trip (startup fired several
# at once; every tab-switch fired one). The fix runs the fetch on a worker
# thread, showing a plain "loading…" line meanwhile. This proves it: with the
# fetch wedged open on its worker, the panel has ALREADY painted "loading…" —
# if the fetch were still on the UI thread, run_test could never reach here.


async def test_boards_view_shows_loading_line_while_fetch_is_blocked(monkeypatch):
    import threading

    import nethackers.tui.screens.hub as hub

    release = threading.Event()
    entered = threading.Event()

    def blocking_board(self, *a, **k):
        entered.set()
        assert release.wait(5.0), "test failed to release the blocked fetch"
        return [{"rank": 1, "owner": "vale", "mean_progression": 0.5,
                 "solution_digest": "a" * 12}]

    monkeypatch.setattr(hub.HubClient, "board", blocking_board)
    app = _HostBoards()
    try:
        async with app.run_test() as pilot:
            # let on_show start the worker; the worker is now wedged in board()
            for _ in range(5):
                await pilot.pause()
                if entered.is_set():
                    break
            assert entered.is_set(), "the fetch worker never started"

            # UI is live despite the in-flight fetch: the loading line is up.
            content = str(app.query_one("#boards_body").content)
            assert "loading" in content
            assert "could not load" not in content

            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            # now the real board content has replaced the loading line
            assert "vale" in _render_to_str(app.query_one("#boards_body").content)
    finally:
        release.set()  # never leave the worker thread wedged


async def test_revisiting_a_tab_shows_cached_content_without_a_loading_flash(monkeypatch):
    """Re-showing a view paints the last-loaded content immediately (stale-
    while-revalidate) instead of flashing "loading…": even with the refresh
    fetch wedged open, the cached table is on screen the moment we switch back,
    and a background refresh still fires."""
    import threading

    import nethackers.tui.screens.hub as hub

    block = threading.Event()    # once set, the NEXT board fetch wedges
    release = threading.Event()
    calls = {"n": 0}

    def board(self, *a, **k):
        calls["n"] += 1
        if block.is_set():
            assert release.wait(5.0), "test failed to release the blocked fetch"
        return [{"rank": 1, "owner": "vale", "mean_progression": 0.5,
                 "solution_digest": "a" * 12}]

    monkeypatch.setattr(hub.HubClient, "board", board)
    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test() as pilot:
        try:
            await pilot.pause()
            # first visit to the Leaderboard: load and cache it
            app.action_show("boards")
            await pilot.pause()                    # let on_show fire -> fetch starts
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "vale" in _render_to_str(app.query_one("#boards_body").content)
            assert calls["n"] == 1

            # leave, then arm the next board fetch to wedge
            app.action_show("map")
            await pilot.pause()
            block.set()

            # revisit: the cached table is up immediately (no "loading…"),
            # although the revalidation fetch is now blocked (so we don't wait)
            app.action_show("boards")
            await pilot.pause()
            shown = _render_to_str(app.query_one("#boards_body").content)
            assert "loading" not in shown
            assert "vale" in shown            # served straight from the cache

            # ...and a background refresh really did fire (it's the wedged one)
            for _ in range(50):
                if calls["n"] == 2:
                    break
                await pilot.pause()
            assert calls["n"] == 2
        finally:
            release.set()
            await app.workers.wait_for_complete()


# --- Empty-payload tests: verify empty-state messages from renderers --------


async def test_boards_view_renders_empty_board_message(monkeypatch):
    """BoardsView renders the empty-state message for an empty board."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    app = _HostBoards()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#boards_body")
        # BoardsView's own empty message must appear
        assert "No ranked solutions yet" in str(body.content)


async def test_elites_view_renders_empty_elites_message(monkeypatch):
    """ElitesView renders the empty-state message for empty elites."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", lambda self, *a, **k: [])
    app = _HostElites()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#elites_body")
        # render_elites's empty message must appear
        assert "no elites recorded yet" in str(body.content)


# --- Wiring-correctness tests: verify each view calls the right method ------


class _FakeHubClient:
    """Fake HubClient that records all method calls with their arguments."""

    instances: list[_FakeHubClient] = []

    def __init__(self, base_url: str, *, timeout: float | None = None) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.board_calls: list[tuple] = []
        self.attainment_calls: list[tuple] = []
        self.elites_calls: list[tuple] = []
        _FakeHubClient.instances.append(self)

    def board(self, objective: str | None = None, metric: str | None = None) -> list:
        self.board_calls.append((objective, metric))
        # non-empty so BoardsView renders via highscore_table (it early-returns
        # an empty-state message on []).
        return [{"rank": 1, "owner": "vale", "mean_progression": 0.5,
                 "solution_digest": "a" * 12}]

    def attainment(self, identity: str | None = None) -> list:
        self.attainment_calls.append((identity,))
        return []

    def elites(self, scope: str) -> list:
        self.elites_calls.append((scope,))
        return []


async def test_boards_view_calls_board_and_passes_you(monkeypatch):
    """BoardsView calls client.board('generalist') with you=login passed to renderer."""
    import nethackers.tui.screens.hub as hub

    captured_renderer_calls = []

    original_render = hub.highscore_table

    def capture_render(entries, **kwargs):
        captured_renderer_calls.append(("highscore_table", entries, kwargs))
        return original_render(entries, **kwargs)

    _FakeHubClient.instances.clear()
    monkeypatch.setattr(hub, "HubClient", _FakeHubClient)
    monkeypatch.setattr(hub, "highscore_table", capture_render)

    app = _HostBoards()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # Verify client.board was called with "generalist"
        assert len(_FakeHubClient.instances) > 0
        client = _FakeHubClient.instances[0]
        assert len(client.board_calls) > 0
        assert client.board_calls[0][0] == "generalist"
        # Also verify render_board was called with you='castiel'
        assert len(captured_renderer_calls) > 0
        assert captured_renderer_calls[0][2].get("you") == "castiel"


async def test_elites_view_calls_elites_and_passes_to_renderer(monkeypatch):
    """ElitesView calls client.elites('generalist') and passes result to render_elites."""
    import nethackers.tui.screens.hub as hub

    captured_renderer_calls = []

    original_render = hub.render_elites

    def capture_render(entries):
        captured_renderer_calls.append(("render_elites", entries))
        return original_render(entries)

    _FakeHubClient.instances.clear()
    monkeypatch.setattr(hub, "HubClient", _FakeHubClient)
    monkeypatch.setattr(hub, "render_elites", capture_render)

    app = _HostElites()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # Verify client.elites was called with "generalist"
        assert len(_FakeHubClient.instances) > 0
        client = _FakeHubClient.instances[0]
        assert len(client.elites_calls) > 0
        assert client.elites_calls[0][0] == "generalist"
        # Also verify render_elites was called with the result
        assert len(captured_renderer_calls) > 0
        assert captured_renderer_calls[0][0] == "render_elites"


# --- Content rendering tests: verify rendered output contains expected data --


async def test_boards_view_renders_entry_content(monkeypatch):
    """BoardsView renders board entry data in the output."""
    import nethackers.tui.screens.hub as hub

    fake_entries = [
        {
            "rank": 1,
            "program_id": "prog_abc123def456",
            "owner": "testuser",
            "ascensions": 5,
            "median_progression": 0.75,
            "mean_progression": 0.72,
        }
    ]
    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: fake_entries)
    app = _HostBoards()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#boards_body")
        # The entry's owner should appear in rendered output
        rendered = _render_to_str(body.content)
        assert "testuser" in rendered


# --- Frontier subtabs: MapView's Universe / Program regimes -----------------
#
# MapView now hosts its own ``Tabs(id="ftabs")`` above the scrollable body,
# switching which {identity: value} map feeds the same render_frontier_grid.
# These patch client methods directly on the real ``hub.HubClient`` (the
# "Content rendering"/"Empty-payload" pattern above), not the wiring-focused
# ``_FakeHubClient``, since what's under test here is rendered *content*
# across a regime switch, not call-argument recording.


def _fake_elites_rank_spread(self, scope: str) -> list:
    """A rank-1 spread across two identities, plus one rank-2 row (must be
    excluded by universe_scores' rank filter -- its value, 0.99, must never
    appear in a Universe render)."""
    return [
        {"identity": "val-dwa-law-fem", "rank": 1, "score": 0.42},
        {"identity": "val-dwa-law-fem", "rank": 2, "score": 0.99},
        {"identity": "arc-hum-law-mal", "rank": 1, "score": 0.15},
    ]


def _fake_board_with_champion(self, *a, **k) -> list:
    return [{"rank": 1, "owner": "vale", "program_id": "abc123def456",
             "mean_progression": 0.5}]


def _fake_program_identities_for_champion(self, program_id: str) -> list:
    """The champion's own per-identity progression -- deliberately a
    *different* number (0.91) on the *same* identity (val-dwa-law-fem) as
    the universe fixture's 0.42, so a test can prove the grid actually
    swapped data sources on regime switch rather than merely re-rendering
    the same numbers."""
    return [{"identity": "val-dwa-law-fem", "progression": 0.91}]


def _fake_baseline(self) -> dict:
    return {
        "owner": "autoascend",
        "per_identity": {
            "val-dwa-law-fem": {"progression": 0.30},
            "wiz-elf-cha-mal": {"progression": 0.25},
        },
        "overall": 0.275,
    }


async def test_map_view_universe_default_shows_a_known_identity_number(monkeypatch):
    """MapView mounts into the Universe regime by default: the grid shows
    universe_scores' rank-1 numbers (never a rank-2 score) under the
    Universe caption."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", _fake_elites_rank_spread)
    monkeypatch.setattr(hub.HubClient, "baseline", _fake_baseline)
    app = _HostMap()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#map_body")
        rendered = _render_to_str(body.content)
        assert "Valkyrie" in rendered
        assert "42.0%" in rendered
        assert "15.0%" in rendered
        assert "99.0%" not in rendered  # rank-2, must be filtered out
        assert "aa = AutoAscend floor" in rendered
        assert "25.0%  aa" in rendered
        om = overall_mean({"val-dwa-law-fem": 0.42, "arc-hum-law-mal": 0.15,
                           "wiz-elf-cha-mal": 0.25})
        assert om is not None
        assert f"overall {om * 100:.1f}%" in rendered


async def test_map_view_activating_program_subtab_shows_champion_grid(monkeypatch):
    """Activating the Program subtab re-renders the same panel from
    champion_scores instead of universe_scores: the champion's distinct
    number replaces the universe number on the same identity, and the
    @owner/program-id caption appears.

    Forward-carry closure (Task 1 -> Task 5): champion() already returns a
    program_id (from the /board migration); champion_scores() must resolve
    it via program_identities (-> /programs/{id}/identities), not the
    retired solution_frontier (-> /solutions/{digest}/frontier, which 404s
    on a program_id) -- updated here to patch program_identities, the
    method MapView's real Program regime now actually calls. (A patched
    method only proves the wiring, not the live route -- see
    tests/hub/test_programs.py's end-to-end test for the real HTTP round
    trip that closes the loop for good.)"""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", _fake_elites_rank_spread)
    monkeypatch.setattr(hub.HubClient, "board", _fake_board_with_champion)
    monkeypatch.setattr(hub.HubClient, "program_identities",
                         _fake_program_identities_for_champion)
    monkeypatch.setattr(hub.HubClient, "baseline", _fake_baseline)

    app = _HostMap()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        body = app.query_one("#map_body")

        # Sanity: mounts into Universe first.
        assert "42.0%" in _render_to_str(body.content)

        app.query_one("#ftabs", Tabs).active = "ft-program"
        await _settle(app, pilot)

        rendered = _render_to_str(body.content)
        assert "91.0%" in rendered  # the champion's number
        assert "42.0%" not in rendered  # the universe number is gone, not merged
        # program_id is opaque and already short -- shown verbatim, never truncated.
        assert "@vale/abc123def456" in rendered
        assert "this one program across all identities" in rendered
        om = overall_mean({"val-dwa-law-fem": 0.91})
        assert om is not None
        assert f"overall {om * 100:.1f}%" in rendered


async def test_map_view_program_subtab_shows_friendly_line_when_no_champion(monkeypatch):
    """board('generalist') == [] means champion() is None: Program shows a
    friendly line, not a crash and not the outage message."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", lambda self, *a, **k: [])
    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    monkeypatch.setattr(hub.HubClient, "baseline", _fake_baseline)

    app = _HostMap()
    async with app.run_test() as pilot:
        app.query_one("#ftabs", Tabs).active = "ft-program"
        await _settle(app, pilot)
        body = app.query_one("#map_body")
        rendered = _render_to_str(body.content)
        assert "no ranked programs yet" in rendered
        assert "could not load" not in rendered
        assert "overall" not in rendered  # no scores -- no mean to report


# --- Nav-collision regression (MANDATORY) ------------------------------------
#
# MapView's own Tabs(id="ftabs") fires TabActivated, which bubbles past
# MapView up to NetHackersApp -- the same handler the main Tabs(id="nav")
# uses to drive the ContentSwitcher. Without a guard keyed on the event's
# *originating* Tabs widget, a Frontier subtab click would be misread as a
# main-nav switch (or worse) and corrupt `body.current`. This proves the
# app's guard actually holds: after a subtab activation, the main nav must
# still be able to switch sections.


async def test_frontier_subtab_activation_does_not_break_main_nav_switching():
    """Regression: activating a MapView subtab must not touch body.current,
    and the main nav must still switch sections normally afterwards."""
    from textual.widgets import ContentSwitcher

    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        body = app.query_one("#body", ContentSwitcher)
        assert body.current == "home"

        await pilot.press("3")  # -> map (see app._SECTIONS)
        await pilot.pause()
        assert body.current == "map"

        # Activate MapView's own Frontier subtab -- must not move body.current.
        app.query_one("#ftabs", Tabs).active = "ft-program"
        await pilot.pause()
        assert body.current == "map"

        # The main nav must still work: switching sections after a subtab
        # click must still reach the ContentSwitcher.
        await pilot.press("2")  # -> boards
        await pilot.pause()
        assert body.current == "boards"
