"""``NetHackersApp``: the dashboard shell -- a ``.tabbar`` header (identity +
hub + section labels) over a ``ContentSwitcher`` hosting the section views
(Home/Boards/Map/Elites/Runs/Evolve), plus an optional pushed
``EvolveScreen`` for ``nethackers evolve``'s TTY path.

Replaces the old single-purpose ``EvolveApp``, which owned its own status
bar and mutation-log tabs directly. That live-monitor UI now lives in
``tui.screens.evolve.EvolveScreen`` (Task 9); this shell hosts it as a
pushed screen (``on_mount``, when ``evolve=(cfg, run)`` is given) so
``nethackers evolve`` and the rest of the dashboard share one App instead of
each subcommand spinning up its own. ``.error``/``.results`` delegate to
that pushed screen so ``cli.py`` can read them after ``app.run()`` exactly
as it did for ``EvolveApp`` -- ``None``/``None`` when this shell was never
given an ``evolve=`` (a plain dashboard launch, no run in flight).

The ``⚔ Evolve`` section (Task 16) is the in-app counterpart to that CLI
path: ``EvolveForm`` sits in the ``ContentSwitcher`` like any other section,
and its own Start button -- not this shell -- pushes an ``EvolveScreen`` on
top of the dashboard once a run is built (``prepare_evolve``), so the two
launch paths converge on the same pushed-screen mechanics right after this
``__init__``'s ``evolve=`` short-circuit.
"""
from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Static, Tab, Tabs

from nethackers.hubclient.credentials import Credentials
from nethackers.tui.screens.evolve import EvolveScreen
from nethackers.tui.screens.evolve_form import EvolveForm
from nethackers.tui.screens.home import HomeView
from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView
from nethackers.tui.screens.runs import RunsView
from nethackers.tui.status import EvolveConfig
from nethackers.tui.theme import CSS

_SECTIONS = [
    ("home", "⌂ Home"), ("boards", "♛ Leaderboard"), ("map", "⇩ Frontier"),
    ("elites", "⚑ Elites"), ("runs", "▶ Runs"), ("evolve", "⚔ Evolve"),
]


class NetHackersApp(App):
    """The dashboard shell. Six sections switched by ``1``..``6`` (or the
    matching tab) over a ``ContentSwitcher``, including the ``⚔ Evolve``
    launch form (``e``/key ``6``); ``l`` remains a stub for the in-app login
    flow a later task fills in (``nethackers login`` on the CLI already
    works today)."""

    CSS = CSS
    BINDINGS = [
        ("q", "quit", "Quit"),
        *[(str(i + 1), f"show('{key}')", label) for i, (key, label) in enumerate(_SECTIONS)],
        ("e", "evolve", "Evolve"),
        ("l", "login", "Login"),
    ]

    def __init__(
        self,
        hub: str,
        creds: Credentials | None = None,
        *,
        start: str = "home",
        evolve: tuple[EvolveConfig, Callable[[dict], object] | None] | None = None,
    ) -> None:
        super().__init__()
        self._hub = hub
        self._creds = creds
        self._start = start
        self._evolve = evolve
        self._evolve_screen: EvolveScreen | None = None

    def compose(self) -> ComposeResult:
        who = f"@{self._creds.login}" if self._creds else "guest"
        host = self._hub.split("//")[-1]
        yield Static(f" {who} · hub:{host}   —   ← → or 1–6 to switch · q quit",
                     classes="idbar")
        yield Tabs(*(Tab(label, id=f"tab-{key}") for key, label in _SECTIONS), id="nav")
        login = self._creds.login if self._creds else None
        with ContentSwitcher(initial=self._start, id="body"):
            yield HomeView(self._hub, login, id="home")
            yield BoardsView(self._hub, login, id="boards")
            yield MapView(self._hub, login, id="map")
            yield ElitesView(self._hub, login, id="elites")
            yield RunsView(id="runs")
            yield EvolveForm(self._hub, self._creds, id="evolve")

    def on_mount(self) -> None:
        self.query_one("#nav", Tabs).active = f"tab-{self._start}"
        if self._evolve is not None:
            cfg, run = self._evolve
            self._evolve_screen = EvolveScreen(cfg, run=run, exit_on_error=True)
            self.push_screen(self._evolve_screen)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Clicking a tab or moving with ← → (Textual's Tabs) switches the
        section; a guard skips the burst of activations Tabs fires before the
        ContentSwitcher has mounted.

        ``MapView`` (the Frontier section) hosts its own subtab bar
        (``Tabs(id="ftabs")``, for its Universe/Program regimes) nested
        inside the ContentSwitcher. Its ``TabActivated`` bubbles up through
        the ContentSwitcher to this same handler, since Textual messages
        bubble to every ancestor regardless of which ``Tabs`` posted them --
        and a ``Tabs`` widget auto-activates its first tab as soon as it
        mounts, so this fires the moment the app starts (all six sections,
        ``MapView`` included, are composed into the ContentSwitcher up
        front, not lazily on first visit), not just when a user actually
        clicks a Frontier subtab. Confirmed by temporarily removing the
        guard below: ``body.current`` got set to ``"ft-universe"``, which
        doesn't exist as a ContentSwitcher child, raising ``NoMatches`` and
        crashing the app on mount -- before any test even switched to the
        Frontier section. Guard on the event's *originating* ``Tabs``
        widget -- ``event.tabs`` (confirmed present on installed Textual
        8.2.8's ``Tabs.TabMessage.__init__``, which every ``TabActivated``
        carries) -- so only the main nav (``id="nav"``) ever drives
        ``body.current``; MapView's own handler switches its internal
        regime itself and never touches this ContentSwitcher.
        """
        if event.tabs.id != "nav":
            return
        if not event.tab.id:
            return
        try:
            body = self.query_one("#body", ContentSwitcher)
        except Exception:
            return
        body.current = event.tab.id.removeprefix("tab-")

    def action_show(self, key: str) -> None:
        # drive the tab bar; its TabActivated switches the ContentSwitcher
        self.query_one("#nav", Tabs).active = f"tab-{key}"

    def action_evolve(self) -> None:
        self.action_show("evolve")

    def action_login(self) -> None:
        pass  # in-app device flow deferred; `nethackers login` on the CLI works today

    @property
    def error(self) -> BaseException | None:
        """The pushed ``EvolveScreen``'s ``.error``, or ``None`` when this
        shell has no evolve run in flight."""
        return self._evolve_screen.error if self._evolve_screen is not None else None

    @property
    def results(self) -> object | None:
        """The pushed ``EvolveScreen``'s ``.results``, or ``None`` -- see
        ``.error``."""
        return self._evolve_screen.results if self._evolve_screen is not None else None
