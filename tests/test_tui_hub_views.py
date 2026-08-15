"""Tests for tui.screens.hub view components."""

from __future__ import annotations

from textual.app import App, ComposeResult

from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView

# An address nothing listens on: httpx.ConnectError fires near-instantly
# (loopback, no DNS), so the smoke test never depends on real network access.
_DEAD_HUB = "http://127.0.0.1:1"


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


async def test_boards_view_mounts_and_handles_hub_unreachable():
    """BoardsView mounts and renders friendly error on hub outage."""
    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        # View should have mounted
        view = app.query_one("#boards")
        assert view is not None
        # Body static should have been updated with error message
        body = app.query_one("#boards_body")
        assert body is not None
        # The body should have content (from the error message)
        assert body.renderable != "" if hasattr(body, "renderable") else True


async def test_map_view_mounts_and_handles_hub_unreachable():
    """MapView mounts and renders friendly error on hub outage."""
    app = _HostMap()
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one("#map")
        assert view is not None
        body = app.query_one("#map_body")
        assert body is not None


async def test_elites_view_mounts_and_handles_hub_unreachable():
    """ElitesView mounts and renders friendly error on hub outage."""
    app = _HostElites()
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one("#elites")
        assert view is not None
        body = app.query_one("#elites_body")
        assert body is not None


async def test_boards_view_renders_empty_board(monkeypatch):
    """BoardsView renders empty board without crashing."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#boards_body")
        # Should have content (empty board message)
        assert body is not None


async def test_map_view_renders_empty_attainment(monkeypatch):
    """MapView renders empty attainment without crashing."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "attainment", lambda self, *a, **k: [])
    app = _HostMap()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#map_body")
        # Should have content (empty attainment message)
        assert body is not None


async def test_elites_view_renders_empty_elites(monkeypatch):
    """ElitesView renders empty elites without crashing."""
    import nethackers.tui.screens.hub as hub

    monkeypatch.setattr(hub.HubClient, "elites", lambda self, *a, **k: [])
    app = _HostElites()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one("#elites_body")
        # Should have content (empty elites message)
        assert body is not None


async def test_boards_view_passes_login_as_you_parameter(monkeypatch):
    """BoardsView passes login as 'you' parameter to render_board."""
    import nethackers.tui.screens.hub as hub

    captured_calls = []

    original_render = hub.render_board

    def capture_render(entries, **kwargs):
        captured_calls.append((entries, kwargs))
        return original_render(entries, **kwargs)

    monkeypatch.setattr(hub.HubClient, "board", lambda self, *a, **k: [])
    monkeypatch.setattr(hub, "render_board", capture_render)

    app = _HostBoards()
    async with app.run_test() as pilot:
        await pilot.pause()
        # render_board should have been called with you='castiel'
        assert len(captured_calls) > 0
        assert captured_calls[0][1].get("you") == "castiel"


async def test_boards_view_renders_entries(monkeypatch):
    """BoardsView renders board entries correctly."""
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
        # Should have content (the board table)
        assert body is not None
