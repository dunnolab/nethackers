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

import contextlib
from collections.abc import Callable

from textual import events
from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    OptionList,
    Select,
    Static,
    Tab,
    Tabs,
)

from nethackers.hubclient.credentials import Credentials
from nethackers.tui.nav import dedup_visible, nearest_in_direction
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
        self._nav_mode = "navigate"  # "navigate" (arrows move the cursor) | "interact"
        self._nav_cursor: Widget | None = None
        self._idbar_prefix = ""

    def compose(self) -> ComposeResult:
        who = f"@{self._creds.login}" if self._creds else "guest"
        host = self._hub.split("//")[-1]
        self._idbar_prefix = f" {who} · hub:{host}"
        yield Static(f"{self._idbar_prefix}   —   ↑↓←→ move · enter use · 1–6 jump · q quit",
                     id="idbar", classes="idbar")
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
        else:
            self.call_after_refresh(self._nav_start)

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
        if self._nav_cursor is not None:  # keep the keyboard cursor on the tab
            self._nav_set_cursor(self.query_one(f"#tab-{key}", Tab))

    def action_evolve(self) -> None:
        self.action_show("evolve")

    def action_login(self) -> None:
        pass  # in-app device flow deferred; `nethackers login` on the CLI works today

    # --- modal 2D keyboard navigation -------------------------------------
    #
    # Navigate mode (default): nothing holds real focus, so no field can
    # swallow a keystroke; a single gold `.-cursor` ring marks the active
    # element and the arrow keys move it by on-screen position (`tui.nav`).
    # Enter *interacts* with the cursor element -- a field/list/select takes
    # real focus (type / pick / open), a button fires, a tab dives into its
    # content. Esc (and a field's own submit/select) returns to Navigate.
    # `1`-`6` still jump straight to a section; `q` still quits (it reaches
    # the App binding precisely because nothing is focused to eat it).

    def _nav_start(self) -> None:
        self._nav_mode = "navigate"
        self.set_focus(None)
        self._nav_set_cursor(self.query_one(f"#tab-{self._start}", Tab))
        self._nav_update_hint()

    def _nav_update_hint(self) -> None:
        legend = (
            "↑↓←→ move · enter use · 1–6 jump · q quit"
            if self._nav_mode == "navigate"
            else "▸ editing — esc back to navigation"
        )
        with contextlib.suppress(Exception):
            self.query_one("#idbar", Static).update(f"{self._idbar_prefix}   —   {legend}")

    def _nav_set_cursor(self, widget: Widget | None) -> None:
        if self._nav_cursor is not None and self._nav_cursor is not widget:
            self._nav_cursor.remove_class("-cursor")
        self._nav_cursor = widget
        if widget is not None:
            widget.add_class("-cursor")
            widget.scroll_visible()

    def _nav_targets(self) -> list[Widget]:
        """Every navigable element on the dashboard right now: the section
        tabs, plus the visible pane's subtabs / controls / focusable cards."""
        targets: list[Widget] = list(self.query("#nav Tab"))
        try:
            body = self.query_one("#body", ContentSwitcher)
        except Exception:
            return dedup_visible(targets)
        current = body.current
        if current:
            pane = body.get_child_by_id(current)
            targets += list(pane.query("#ftabs Tab"))
            for kind in (Input, Select, OptionList, Button):
                targets += list(pane.query(kind))
            if getattr(pane, "can_focus", False):
                targets.append(pane)
            targets += [w for w in pane.query(".panel") if getattr(w, "can_focus", False)]
        return dedup_visible(targets)

    @staticmethod
    def _is_nav_tab(widget: Widget) -> bool:
        return isinstance(widget, Tab) and (widget.id or "").startswith("tab-")

    def _active_section_tab(self) -> Widget | None:
        body = self.query_one("#body", ContentSwitcher)
        if body.current:
            try:
                return self.query_one(f"#tab-{body.current}", Tab)
            except Exception:
                return None
        return None

    def _nav_move(self, direction: str) -> None:
        cur = self._nav_cursor
        if cur is None:
            self._nav_start()
            return
        others = [w for w in self._nav_targets() if w is not cur]
        nav_tabs = [w for w in others if self._is_nav_tab(w)]
        content = [w for w in others if not self._is_nav_tab(w)]
        if self._is_nav_tab(cur):
            # the top row: left/right along the tabs, down dives into content
            if direction in ("left", "right"):
                nxt = nearest_in_direction(cur, nav_tabs, direction)
            elif direction == "down":
                nxt = content[0] if content else None  # dive to the section's first element
            else:
                nxt = None  # already at the top
        else:
            nxt = nearest_in_direction(cur, content, direction)
            if nxt is None and direction == "up":  # leave the top of the content
                nxt = self._active_section_tab()   # back to this section's own tab
        if nxt is None:
            return
        self._nav_set_cursor(nxt)
        self._nav_switch_tab_live(nxt)

    def _nav_switch_tab_live(self, widget: Widget) -> None:
        """Moving the cursor onto a section/subtab switches to it live."""
        wid = widget.id or ""
        if wid.startswith("tab-"):
            self.query_one("#nav", Tabs).active = wid
        elif wid.startswith("ft-"):
            with contextlib.suppress(Exception):
                self.query_one("#ftabs", Tabs).active = wid

    def _nav_activate(self) -> None:
        w = self._nav_cursor
        if w is None:
            return
        if isinstance(w, Tab):
            self._nav_dive()  # into the section's content
        elif isinstance(w, Button):
            w.press()
        else:  # Input / Select / OptionList / a focusable card -> interact
            self._nav_mode = "interact"
            w.focus()
            self._nav_update_hint()

    def _nav_dive(self) -> None:
        for w in self._nav_targets():
            if not isinstance(w, Tab):
                self._nav_set_cursor(w)
                return

    def _nav_to_navigate(self) -> None:
        self._nav_mode = "navigate"
        self.set_focus(None)
        if self._nav_cursor is not None:
            self._nav_cursor.add_class("-cursor")
        self._nav_update_hint()

    def on_key(self, event: events.Key) -> None:
        if len(self.screen_stack) > 1:
            return  # a screen is pushed (e.g. the evolve monitor) -- it owns its keys
        if self._nav_mode == "interact":
            if event.key == "escape":
                self._nav_to_navigate()
                event.stop()
            return  # otherwise the focused widget handles it
        if event.key in ("up", "down", "left", "right"):
            self._nav_move(event.key)
            event.stop()
        elif event.key == "enter":
            self._nav_activate()
            event.stop()
        # q / 1-6 / e / l are left for the App bindings (nothing is focused)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._nav_mode == "interact":
            self._nav_to_navigate()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._nav_mode == "interact":
            self._nav_to_navigate()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._nav_mode == "interact":
            self._nav_to_navigate()

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
