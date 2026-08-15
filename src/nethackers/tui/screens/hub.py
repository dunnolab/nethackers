"""Hub views for the TUI: BoardsView, MapView, ElitesView."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from nethackers.hubclient.client import HubClient
from nethackers.hubclient.render import render_attainment, render_board, render_elites


class _HubView(VerticalScroll):
    """Base view for hub-sourced content: fetches on mount/show, renders via
    existing rich renderers, and gracefully handles hub outages."""

    def __init__(self, hub: str, login: str | None, **kw):
        super().__init__(**kw)
        self._hub = hub
        self._login = login
        self._body: Static | None = None

    def compose(self) -> ComposeResult:
        self._body = Static(id=f"{self.id}_body")
        yield self._body

    def on_mount(self) -> None:
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        try:
            renderable = self._render_hub(HubClient(self._hub))
        except Exception as exc:
            renderable = f"[dim]could not load: {exc}[/dim]"
        if self._body is not None:
            self._body.update(renderable)

    def _render_hub(self, client: HubClient):
        raise NotImplementedError


class BoardsView(_HubView):
    """Random boards leaderboard view."""

    def _render_hub(self, client: HubClient):
        return render_board(client.board("random"), you=self._login)


class MapView(_HubView):
    """Attainment map view."""

    def _render_hub(self, client: HubClient):
        return render_attainment(client.attainment(None))


class ElitesView(_HubView):
    """Elite solutions pool view."""

    def _render_hub(self, client: HubClient):
        return render_elites(client.elites("all"))
