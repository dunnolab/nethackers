"""Tests for tui.screens.hub view components."""

from __future__ import annotations

import io

from rich.console import Console
from textual.app import App, ComposeResult

from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView

# An address nothing listens on: httpx.ConnectError fires near-instantly
# (loopback, no DNS), so the smoke test never depends on real network access.
_DEAD_HUB = "http://127.0.0.1:1"


def _render_to_str(renderable) -> str:
    """Render a rich renderable to plain text for assertion."""
    string_file: io.StringIO = io.StringIO()
    console = Console(width=80, file=string_file, force_terminal=True)
    console.print(renderable)
    return string_file.getvalue()


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
        await pilot.pause()
        body = app.query_one("#boards_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


async def test_map_view_handles_hub_unreachable():
    """MapView renders friendly error on hub outage, not a crash."""
    app = _HostMap()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#map_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


async def test_elites_view_handles_hub_unreachable():
    """ElitesView renders friendly error on hub outage, not a crash."""
    app = _HostElites()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#elites_body")
        # Should render the except-path message, not crash
        assert "could not load" in str(body.content)


# --- Empty-payload tests: verify empty-state messages from renderers --------


async def test_boards_view_renders_empty_board_message(monkeypatch):
    """BoardsView renders the empty-state message for an empty board."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#boards_body")
        # render_board's empty message must appear
        assert "no board entries yet" in str(body.content)


async def test_map_view_renders_empty_attainment_message(monkeypatch):
    """MapView renders the empty-state message for empty attainment."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "attainment", lambda self, *a, **k: [])
    app = _HostMap()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#map_body")
        # render_attainment's empty message must appear
        assert "no attainment cells yet" in str(body.content)


async def test_elites_view_renders_empty_elites_message(monkeypatch):
    """ElitesView renders the empty-state message for empty elites."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", lambda self, *a, **k: [])
    app = _HostElites()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#elites_body")
        # render_elites's empty message must appear
        assert "no elites recorded yet" in str(body.content)


# --- Wiring-correctness tests: verify each view calls the right method ------


async def test_boards_view_calls_board_and_passes_you(monkeypatch):
    """BoardsView calls client.board('random') with you=login passed to renderer."""
    import nethackers.tui.screens.hub as hub

    captured_calls = []

    original_render = hub.render_board

    def capture_render(entries, **kwargs):
        captured_calls.append(("render_board", entries, kwargs))
        return original_render(entries, **kwargs)

    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    monkeypatch.setattr(hub, "render_board", capture_render)

    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        # render_board should have been called with you='castiel'
        assert len(captured_calls) > 0
        assert captured_calls[0][0] == "render_board"
        assert captured_calls[0][2].get("you") == "castiel"


async def test_map_view_calls_attainment_and_passes_to_renderer(monkeypatch):
    """MapView calls client.attainment(None) and passes result to render_attainment."""
    import nethackers.tui.screens.hub as hub

    # Track what the client method returns and what the renderer receives
    fake_cells = [{"identity": "test", "milestone": "test"}]
    captured_renderer_calls = []

    original_render = hub.render_attainment

    def capture_render(cells):
        captured_renderer_calls.append(("render_attainment", cells))
        return original_render(cells)

    monkeypatch.setattr(hub.HubClient, "attainment", lambda self, *a, **k: fake_cells)
    monkeypatch.setattr(hub, "render_attainment", capture_render)

    app = _HostMap()
    async with app.run_test() as pilot:
        await pilot.pause()
        # render_attainment should have been called with the fake_cells
        assert len(captured_renderer_calls) > 0
        assert captured_renderer_calls[0][0] == "render_attainment"
        assert captured_renderer_calls[0][1] == fake_cells


async def test_elites_view_calls_elites_and_passes_to_renderer(monkeypatch):
    """ElitesView calls client.elites('all') and passes result to render_elites."""
    import nethackers.tui.screens.hub as hub

    # Track what the client method returns and what the renderer receives
    fake_elites = [{"rank": 1, "identity": "test", "solution_digest": "abc", "score": 0.5}]
    captured_renderer_calls = []

    original_render = hub.render_elites

    def capture_render(entries):
        captured_renderer_calls.append(("render_elites", entries))
        return original_render(entries)

    monkeypatch.setattr(hub.HubClient, "elites", lambda self, *a, **k: fake_elites)
    monkeypatch.setattr(hub, "render_elites", capture_render)

    app = _HostElites()
    async with app.run_test() as pilot:
        await pilot.pause()
        # render_elites should have been called with the fake_elites
        assert len(captured_renderer_calls) > 0
        assert captured_renderer_calls[0][0] == "render_elites"
        assert captured_renderer_calls[0][1] == fake_elites


# --- Content rendering tests: verify rendered output contains expected data --


async def test_boards_view_renders_entry_content(monkeypatch):
    """BoardsView renders board entry data in the output."""
    import nethackers.tui.screens.hub as hub

    fake_entries = [
        {
            "rank": 1,
            "solution_digest": "abc123def456",
            "owner": "testuser",
            "ascensions": 5,
            "median_progression": 0.75,
            "mean_progression": 0.72,
        }
    ]
    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: fake_entries)
    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#boards_body")
        # The entry's owner should appear in rendered output
        rendered = _render_to_str(body.content)
        assert "testuser" in rendered
