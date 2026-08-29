"""Hub views for the TUI: BoardsView, MapView, ElitesView -- each a framed,
titled panel that fetches from the hub and renders via the existing rich
renderers, degrading to a friendly message on a hub outage.

The fetch runs on a **worker thread** (``@work(thread=True)``), never the UI
event loop -- a synchronous hub round-trip there froze the terminal for the
whole request (~0.3s on a fast hub, up to the timeout on a slow one), which is
what made both startup and tab-switches feel laggy. Each view shows a plain
``loading…`` line the instant it's shown and swaps in the real table when the
worker returns. Fetching is also **lazy** -- driven by ``on_show``, not
``on_mount`` -- so at startup only the visible section talks to the hub (the
others stay silent until first opened), instead of every section firing a
blocking request up front before the first frame could paint."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static, Tab, Tabs
from textual.worker import get_current_worker

from nethackers.hubclient.client import HubClient
from nethackers.hubclient.frontier import (
    baseline_scores,
    champion,
    champion_scores,
    overall_mean,
    universe_scores,
    with_baseline_floor,
)
from nethackers.hubclient.render import render_elites, render_frontier_grid
from nethackers.tui.art import highscore_table

# A slow hub must not leave the panel loading forever: the worker gives up after
# this many seconds and the view degrades to "could not load".
_HUB_TIMEOUT = 4.0


class _HubView(VerticalScroll):
    """Base view for hub-sourced content: a ``.panel``-framed scroll that, when
    shown, fetches on a worker thread (showing ``loading…`` meanwhile) and
    renders via the existing rich renderers, degrading to a "could not load"
    line instead of crashing on a hub outage -- without ever blocking the UI."""

    PANEL_TITLE = ""
    DEFAULT_CSS = """
    _HubView { margin: 1 2; padding: 0 1; height: 1fr; }
    """

    def __init__(self, hub: str, login: str | None, **kw):
        super().__init__(**kw)
        self._hub = hub
        self._login = login
        self._body: Static | None = None
        self._shown = False  # gates subclass refreshes (e.g. MapView subtabs) to when visible
        # Session-scoped stale-while-revalidate cache: the last content we
        # successfully rendered, so re-showing this view paints instantly (no
        # "loading…" flicker) while a fresh fetch quietly updates it in place.
        # Keyed by _cache_key() so a view with sub-modes (MapView's regimes)
        # caches each independently. Lives on the instance, which the
        # ContentSwitcher keeps mounted for the whole session.
        self._cache: dict[str, RenderableType] = {}
        self.add_class("panel")

    def compose(self) -> ComposeResult:
        self._body = Static(id=f"{self.id}_body")
        yield self._body

    def on_mount(self) -> None:
        self.border_title = self.PANEL_TITLE

    def on_show(self) -> None:
        self._shown = True
        self.refresh_hub()

    def _cache_key(self) -> str:
        """Distinguishes what ``_render_hub`` produces so each variant is cached
        separately. A single view has one; MapView overrides it per regime."""
        return ""

    def refresh_hub(self) -> None:
        """Paint the cached content if we have it (else a loading line), then
        (re)fetch on a worker thread to refresh it in place."""
        key = self._cache_key()
        cached = self._cache.get(key)
        if self._body is not None:
            self._body.update(cached if cached is not None else "[dim]loading…[/dim]")
        self._fetch(key)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch(self, key: str) -> None:
        worker = get_current_worker()
        try:
            renderable = self._render_hub(HubClient(self._hub, timeout=_HUB_TIMEOUT))
            err: Exception | None = None
        except Exception as exc:  # a hub outage/timeout -> friendly line, never a crash
            renderable, err = None, exc
        if not worker.is_cancelled:
            self.app.call_from_thread(self._apply, renderable, err, key)

    def _apply(self, renderable, err: Exception | None, key: str) -> None:
        if err is not None:
            # Don't clobber good cached content on a background refresh failure;
            # only surface the outage when this variant has nothing cached yet.
            if key not in self._cache and self._body is not None and self._cache_key() == key:
                self._body.update(f"[dim]could not load — {err}[/dim]")
            return
        self._cache[key] = renderable
        if self._body is not None and self._cache_key() == key:  # still the shown variant
            self._body.update(renderable)

    def _render_hub(self, client: HubClient):
        raise NotImplementedError


class BoardsView(_HubView):
    """The public ranking board for the north-star ``generalist`` objective."""

    PANEL_TITLE = "♛ Leaderboard — generalist"

    def _render_hub(self, client: HubClient):
        entries = client.board("generalist")
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

    def _cache_key(self) -> str:
        return self._regime  # Universe and Program are cached independently

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
        # ftabs auto-activates its first tab at mount -- before this view is ever
        # shown, and while it's still hidden in the ContentSwitcher. Only fetch
        # (and animate) once we're actually visible; the first on_show picks up
        # whatever regime is set by then.
        if self._shown:
            self.refresh_hub()

    def _render_hub(self, client: HubClient):
        aa = baseline_scores(client)
        if self._regime == "program":
            champ = champion(client)
            if champ is None:
                return Text("no ranked programs yet.", style="dim")
            pid, owner = champ
            scores: dict[str, float | None] = dict(champion_scores(client, pid))
            note = f"@{owner}/{pid} — this one program across all identities"
            om = overall_mean(scores)
            if om is not None:
                note = f"{note} · overall {om * 100:.1f}%"
            aa_mean = overall_mean(aa)
            if aa_mean is not None:
                note = f"{note} · AutoAscend overall {aa_mean * 100:.1f}%"
            return render_frontier_grid(scores, note=note, baseline_scores=aa)
        universe: dict[str, float | None] = dict(universe_scores(client))
        best_of_all = with_baseline_floor(universe, aa)
        note = "best result per identity · aa = AutoAscend floor · signed value = Δ vs AA"
        om = overall_mean(best_of_all)
        if om is not None:
            note = f"{note} · overall {om * 100:.1f}%"
        return render_frontier_grid(
            universe, note=note, baseline_scores=aa, baseline_floor=True
        )


class ElitesView(_HubView):
    """The elite pool across every identity."""

    PANEL_TITLE = "⚑ Elite Pool"

    def _render_hub(self, client: HubClient):
        return render_elites(client.elites("generalist"))
