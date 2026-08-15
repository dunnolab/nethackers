"""Hub views for the TUI: BoardsView, MapView, ElitesView -- each a framed,
titled panel that fetches from the hub on mount/show and renders via the
existing rich renderers, degrading to a friendly message on a hub outage."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from nethackers.hubclient.client import HubClient
from nethackers.hubclient.render import render_attainment, render_elites
from nethackers.tui.art import highscore_table


class _HubView(VerticalScroll):
    """Base view for hub-sourced content: a ``.panel``-framed scroll that
    fetches on mount/show, renders via the existing rich renderers, and shows
    a "could not load" line instead of crashing on a hub outage."""

    PANEL_TITLE = ""
    DEFAULT_CSS = """
    _HubView { margin: 1 2; padding: 0 1; height: 1fr; }
    """

    def __init__(self, hub: str, login: str | None, **kw):
        super().__init__(**kw)
        self._hub = hub
        self._login = login
        self._body: Static | None = None
        self.add_class("panel")

    def compose(self) -> ComposeResult:
        self._body = Static(id=f"{self.id}_body")
        yield self._body

    def on_mount(self) -> None:
        self.border_title = self.PANEL_TITLE
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        try:
            renderable = self._render_hub(HubClient(self._hub))
        except Exception as exc:
            renderable = f"[dim]could not load — {exc}[/dim]"
        if self._body is not None:
            self._body.update(renderable)

    def _render_hub(self, client: HubClient):
        raise NotImplementedError


class BoardsView(_HubView):
    """The public ranking board for the north-star ``random`` objective."""

    PANEL_TITLE = "♛ Leaderboard — random"

    def _render_hub(self, client: HubClient):
        entries = client.board("random")
        if not entries:
            return "[dim]No ranked solutions yet.[/]"
        return highscore_table(entries, you=self._login)


class MapView(_HubView):
    """The attainment map: identity × milestone coverage."""

    PANEL_TITLE = "⇩ Frontier — how far we've collectively gotten"

    def _render_hub(self, client: HubClient):
        return render_attainment(client.attainment(None))


class ElitesView(_HubView):
    """The elite pool across every identity."""

    PANEL_TITLE = "⚑ Elite Pool"

    def _render_hub(self, client: HubClient):
        return render_elites(client.elites("all"))
