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

from textual import events, work
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

from nethackers.harness.launch import EvolvePlan
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.nav import dedup_visible, nearest_in_direction
from nethackers.tui.run import Run
from nethackers.tui.screens.evolve_form import EvolveForm
from nethackers.tui.screens.home import HomeView
from nethackers.tui.screens.hub import BoardsView, ElitesView, MapView
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.screens.runs import RunsView
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
        evolve: EvolvePlan | None = None,
    ) -> None:
        super().__init__()
        self._hub = hub
        self._creds = creds
        self._start = start
        self._evolve = evolve
        self._runs: dict[str, Run] = {}  # background evolution runs, this session
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
        self.call_after_refresh(self._nav_start)  # dashboard nav is always ready underneath
        if self._evolve is not None:
            # auto-start the run + open its monitor over the dashboard; esc
            # detaches to the dashboard (Runs tab) with the run still going.
            plan = self._evolve
            self.call_after_refresh(lambda: self.start_run(plan))

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
        # keep the keyboard cursor on the active tab -- however the tab was
        # activated (a mouse click, or Textual's own ←/→ when #nav holds focus),
        # so the gold cursor never sits on a different tab than the shown
        # section (the "two tabs highlighted" glitch).
        if self._nav_mode == "navigate" and self._nav_cursor is not None:
            self._nav_set_cursor(event.tab)

    def action_show(self, key: str) -> None:
        # drive the tab bar; its TabActivated switches the ContentSwitcher (and
        # now also syncs the keyboard cursor onto the tab).
        self.query_one("#nav", Tabs).active = f"tab-{key}"

    def reassert_navigate(self) -> None:
        """Re-take the modal navigate mode after returning to the dashboard from
        a pushed screen (a run monitor). Textual restores focus to #nav on
        resume, which would let its Tabs eat ←/→ and desync the cursor from the
        active section -- so blur it and reclaim navigate mode."""
        if len(self.screen_stack) > 1:
            return  # still on a pushed screen
        if self._nav_cursor is None:
            self._nav_start()
        else:
            self._nav_to_navigate()

    def leave_to_nav(self) -> None:
        """Hand control from a focused form field back to the modal keyboard
        nav: navigate mode, nothing focused -- so q / 1-6 and the arrow cursor
        work again (focusing #nav instead would let Textual's Tabs steal ←/→
        and desync the cursor from the active section)."""
        if self._nav_cursor is None:
            self._nav_start()
        else:
            self._nav_to_navigate()

    def action_evolve(self) -> None:
        self.action_show("evolve")

    def action_login(self) -> None:
        pass  # in-app device flow deferred; `nethackers login` on the CLI works today

    # --- background runs ---------------------------------------------------
    #
    # A run's worker + accumulated state live on an app-level Run (tui.run),
    # not on the monitor screen -- so you can open a run's monitor, leave it
    # (esc), roam the dashboard, and reopen it, all while it keeps running.
    # Multiple runs can be in flight. Quitting the app stops them (session
    # scoped, not a detached daemon).

    def start_run(self, plan: EvolvePlan) -> Run:
        run = Run(plan.rid, plan.cfg)
        self._runs[run.rid] = run
        self._run_worker(run, plan.run)
        self.open_run(run.rid)
        return run

    @work(thread=True, exit_on_error=False)
    def _run_worker(self, run: Run, run_fn: Callable[..., object]) -> None:
        try:
            results = run_fn({
                "on_state": lambda s: self.call_from_thread(self._on_run_state, run, s),
                "on_episode": lambda label, ep: self.call_from_thread(
                    self._on_run_episode, run, label, ep),
                "on_log": lambda tag, line: self.call_from_thread(
                    self._on_run_log, run, tag, line),
                "stop": run.stop,
            })
        except Exception as exc:
            self.call_from_thread(self._finish_run, run, None, exc)
        else:
            self.call_from_thread(self._finish_run, run, results, None)

    def _monitor_for(self, run: Run) -> RunMonitor | None:
        """The mounted monitor for ``run``, iff it's the screen on top."""
        scr = self.screen
        return scr if isinstance(scr, RunMonitor) and scr.run is run else None

    def _on_run_state(self, run: Run, state: dict) -> None:
        run.apply_state(state)
        m = self._monitor_for(run)
        if m is not None:
            m.render_state()

    def _on_run_episode(self, run: Run, label: str, ep: dict) -> None:
        run.apply_episode(label, ep)
        m = self._monitor_for(run)
        if m is not None:
            m.render_episode(label, ep)

    def _on_run_log(self, run: Run, tag: str, line: str) -> None:
        run.apply_log(tag, line)
        m = self._monitor_for(run)
        if m is not None:
            m.render_log(tag)

    def _finish_run(self, run: Run, results: object | None,
                    error: BaseException | None) -> None:
        run.finish(results=results, error=error)
        if error is not None:
            self.notify(f"run {run.rid} failed: {error}", severity="error", timeout=10)
        m = self._monitor_for(run)
        if m is not None:
            m.render_state()

    def open_run(self, rid: str) -> None:
        run = self._runs.get(rid)
        if run is None:
            return
        scr = self.screen
        if isinstance(scr, RunMonitor):
            # a monitor is already up -> SWAP to this run's, never stack (else
            # starting/opening a 2nd run buries the 1st and esc walks back through
            # stale monitors instead of returning to the dashboard).
            if scr.run is run:
                return  # already showing this run
            self.switch_screen(RunMonitor(run))
        else:
            self.push_screen(RunMonitor(run))

    def stop_run(self, rid: str) -> None:
        run = self._runs.get(rid)
        if run is not None:
            run.stop.set()

    def action_quit(self) -> None:  # type: ignore[override]
        for run in self._runs.values():
            run.stop.set()  # best-effort: signal the workers before teardown
        self.exit()

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

    @staticmethod
    def _is_subtab(widget: Widget) -> bool:
        # a section's own subtab (Frontier's Universe/Program: id "ft-…")
        return isinstance(widget, Tab) and (widget.id or "").startswith("ft-")

    def _active_subtab(self) -> Widget | None:
        """The currently-active subtab widget (Frontier's #ftabs), or None."""
        try:
            active = self.query_one("#ftabs", Tabs).active
        except Exception:
            return None
        return next((w for w in self._nav_targets()
                     if self._is_subtab(w) and w.id == active), None)

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
        subtabs = [w for w in others if self._is_subtab(w)]
        content = [w for w in others
                   if not self._is_nav_tab(w) and not self._is_subtab(w)]
        if self._is_nav_tab(cur):
            # the main tab row: left/right along the tabs, down dives in
            if direction in ("left", "right"):
                nxt = nearest_in_direction(cur, nav_tabs, direction)
            elif direction == "down":  # to the active subtab if any, else first control
                nxt = self._active_subtab() or (content[0] if content else None)
            else:
                nxt = None  # already at the top
        elif self._is_subtab(cur):
            # a section's own subtab row (Frontier Universe/Program)
            if direction in ("left", "right"):
                nxt = nearest_in_direction(cur, subtabs, direction)
            elif direction == "down":
                nxt = content[0] if content else None  # into the section body
            elif direction == "up":
                nxt = self._active_section_tab()       # back up to the main tab
            else:
                nxt = None
        else:
            nxt = nearest_in_direction(cur, content, direction)
            if nxt is None and direction == "up":  # leaving the top of the body:
                # land on the ACTIVE subtab (never the geometric nearest, which
                # would silently flip the regime), else the section's main tab
                nxt = self._active_subtab() or self._active_section_tab()
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
        """The first failed run's error, or ``None`` -- what the CLI's
        headless-fallback path reads after ``app.run()``."""
        return next((r.error for r in self._runs.values() if r.error is not None), None)

    @property
    def results(self) -> object | None:
        """The first finished run's results, or ``None``."""
        return next((r.results for r in self._runs.values() if r.results is not None), None)
