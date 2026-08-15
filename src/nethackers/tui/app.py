"""``NetHackersApp``: the dashboard shell -- a ``.tabbar`` header (identity +
hub + section labels) over a ``ContentSwitcher`` hosting the section views
(Home/Boards/Map/Elites/Runs), plus an optional pushed ``EvolveScreen`` for
``nethackers evolve``'s TTY path.

Replaces the old single-purpose ``EvolveApp``, which owned its own status
bar and mutation-log tabs directly. That live-monitor UI now lives in
``tui.screens.evolve.EvolveScreen`` (Task 9); this shell hosts it as a
pushed screen (``on_mount``, when ``evolve=(cfg, run)`` is given) so
``nethackers evolve`` and the rest of the dashboard share one App instead of
each subcommand spinning up its own. ``.error``/``.results`` delegate to
that pushed screen so ``cli.py`` can read them after ``app.run()`` exactly
as it did for ``EvolveApp`` -- ``None``/``None`` when this shell was never
given an ``evolve=`` (a plain dashboard launch, no run in flight).
"""
from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Static

from nethackers.hubclient.credentials import Credentials
from nethackers.tui.screens.evolve import EvolveScreen
from nethackers.tui.screens.home import HomeView
from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView
from nethackers.tui.screens.runs import RunsView
from nethackers.tui.status import EvolveConfig
from nethackers.tui.theme import CSS

_SECTIONS = [
    ("home", "⌂ Home"), ("boards", "♛ Boards"), ("map", "▚ Map"),
    ("elites", "⚑ Elites"), ("runs", "▶ Runs"),
]


class NetHackersApp(App):
    """The dashboard shell. Five sections switched by ``1``..``5`` (or the
    matching tab) over a ``ContentSwitcher``; ``e``/``l`` are stubs for the
    in-app evolve-launch/login flows a later task fills in (``nethackers
    evolve``/``nethackers login`` on the CLI already work today)."""

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
        labels = "   ".join(label for _key, label in _SECTIONS)
        yield Static(f"  {who} · hub:{host}   {labels}", classes="tabbar")
        login = self._creds.login if self._creds else None
        with ContentSwitcher(initial=self._start, id="body"):
            yield HomeView(self._hub, login, id="home")
            yield BoardsView(self._hub, login, id="boards")
            yield MapView(self._hub, login, id="map")
            yield ElitesView(self._hub, login, id="elites")
            yield RunsView(id="runs")

    def on_mount(self) -> None:
        if self._evolve is not None:
            cfg, run = self._evolve
            self._evolve_screen = EvolveScreen(cfg, run=run)
            self.push_screen(self._evolve_screen)

    def action_show(self, key: str) -> None:
        self.query_one("#body", ContentSwitcher).current = key

    def action_evolve(self) -> None:
        pass  # in-app launch form deferred to a later task; `nethackers evolve` still works

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
