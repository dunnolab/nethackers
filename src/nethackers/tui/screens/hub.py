"""Hub views for the TUI: BoardsView, MapView, ElitesView -- each a framed,
titled panel that fetches from the hub on mount/show and renders via the
existing rich renderers, degrading to a friendly message on a hub outage."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static, Tab, Tabs

from nethackers.hubclient.client import HubClient, _short_digest
from nethackers.hubclient.frontier import champion, champion_scores, overall_mean, universe_scores
from nethackers.hubclient.render import render_elites, render_frontier_grid
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
    """The Frontier: a role x variation number grid of progression scores,
    rendered by the same ``render_frontier_grid`` in either of two regimes
    switched by this view's own subtab bar (``id="ftabs"``, distinct from
    the app's main-nav ``Tabs(id="nav")``):

    - Universe (default): every identity's best program -- ``universe_scores``.
    - Program: one program, the current champion, across every identity --
      ``champion``/``champion_scores``.
    """

    PANEL_TITLE = "⇩ Frontier — how far we've collectively gotten"

    def __init__(self, hub: str, login: str | None, **kw):
        super().__init__(hub, login, **kw)
        self._regime = "universe"

    def compose(self) -> ComposeResult:
        yield Tabs(
            Tab("◆ Universe", id="ft-universe"),
            Tab("◇ Program", id="ft-program"),
            id="ftabs",
        )
        yield from super().compose()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Switches this view's own regime and re-fetches/re-renders.

        Guarded to only react to this view's own subtab bar. This event
        also bubbles past this widget up to the app -- see
        ``NetHackersApp.on_tabs_tab_activated``'s matching guard
        (``event.tabs.id == "nav"``), which is what actually keeps a
        Frontier subtab click from being misread as a main-nav section
        switch. Deliberately does NOT call ``event.stop()``: letting the
        event keep bubbling is what makes that app-level guard the real,
        exercised safety net (see the nav-regression test in
        ``tests/test_tui_hub_views.py``) rather than an untested guard that
        happens to never see traffic.
        """
        if event.tabs.id != "ftabs" or not event.tab.id:
            return
        self._regime = event.tab.id.removeprefix("ft-")
        self._refresh()

    def _render_hub(self, client: HubClient):
        if self._regime == "program":
            champ = champion(client)
            if champ is None:
                return Text("no ranked programs yet.", style="dim")
            digest, owner = champ
            scores: dict[str, float | None] = dict(champion_scores(client, digest))
            note = f"@{owner}/{_short_digest(digest)} — this one program across all identities"
            om = overall_mean(scores)
            if om is not None:
                note = f"{note} · overall {om:.2f}"
            return render_frontier_grid(scores, note=note)
        universe: dict[str, float | None] = dict(universe_scores(client))
        note = "each number = the best program's mean on that identity"
        om = overall_mean(universe)
        if om is not None:
            note = f"{note} · overall {om:.2f}"
        return render_frontier_grid(universe, note=note)


class ElitesView(_HubView):
    """The elite pool across every identity."""

    PANEL_TITLE = "⚑ Elite Pool"

    def _render_hub(self, client: HubClient):
        return render_elites(client.elites("all"))
