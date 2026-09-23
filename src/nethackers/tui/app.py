"""``NetHackersApp``: the dashboard shell -- a ``.tabbar`` header (identity +
hub + section labels) over a ``ContentSwitcher`` hosting the section views
(Home/Runs/Evolve), plus an optional pushed ``EvolveScreen`` for
``nethackers evolve``'s TTY path. Hub-browsing views (leaderboard / frontier /
elites) live in the CLI (``nethackers frontier`` etc.) and the website, not
this local run-focused dashboard.

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
from pathlib import Path

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

from nethackers.config import load_stage
from nethackers.harness.launch import EvolvePlan
from nethackers.harness.loop import IterationResult
from nethackers.hubclient.client import HubClient, HubUnreachable
from nethackers.hubclient.credentials import Credentials
from nethackers.tui._util import _TOAST_DETAIL_MAXLEN as _TOAST_DETAIL_MAXLEN, failure_detail
from nethackers.tui.nav import dedup_visible, nearest_in_direction
from nethackers.tui.run import Run
from nethackers.tui.screens.evolve_form import EvolveForm
from nethackers.tui.screens.home import HomeView
from nethackers.tui.screens.login import LoginModal
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.screens.runs import RunsView
from nethackers.tui.theme import CSS

_SECTIONS = [
    ("home", "⌂ Home"), ("evolve", "⚔ Evolve"), ("runs", "▶ Runs"),
]

# Give up on the hub-mode probe fast so the idbar never lingers on it; the
# ambient bar just stays at its baseline @login/guest text if the hub is
# slow (mirrors tui.screens.home._HUB_TIMEOUT).
_HUB_MODE_TIMEOUT = 4.0


def _idbar_who(login: str | None, hub_mode: str | None, *, unreachable: bool) -> str:
    """The idbar's identity segment -- EFFECTIVE identity (who you are TO
    THE HUB you're pointed at), condensed for a one-line ambient bar: the
    same model ``cli.py``'s ``whoami`` renders in full (``_where_line``),
    here without the parenthetical fix-it hint (no room on a persistent
    status line) and with the unreachable case APPENDED rather than
    replacing the baseline text -- so an in-flight probe can never make an
    idbar assertion racy: whatever this returns always still contains
    plain ``_idbar_text``'s own baseline "@login"/"guest" substring."""
    base = f"@{login}" if login else "guest"
    if unreachable:
        return f"{base}  [yellow]⚠ hub unreachable[/]"
    if hub_mode == "offline":
        if login is None:
            return "[b]OFFLINE[/]"
        return f"{base}  [yellow]⚠ OFFLINE hub[/]"
    return base


class NetHackersApp(App):
    """The dashboard shell. Three sections switched by ``1``..``3`` (or the
    matching tab) over a ``ContentSwitcher``, including the ``⚔ Evolve``
    launch form (``e``/key ``2``); ``l`` opens the in-app GitHub device-flow
    login (``LoginModal``), and Home's own button logs in or out."""

    CSS = CSS
    # Keep a pushed screen's footer minimal (a run monitor shows only its own
    # esc/s/q): drop Textual's built-in "⌃p palette" hint, and check_action
    # hides the dashboard's own 1/2/3 · e · l there too.
    ENABLE_COMMAND_PALETTE = False
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
        self._stage = load_stage()  # for the idbar's non-prod indicator, computed once
        self._runs: dict[str, Run] = {}  # background evolution runs, this session
        self._nav_mode = "navigate"  # "navigate" (arrows move the cursor) | "interact"
        self._nav_cursor: Widget | None = None
        self._idbar_prefix = ""
        # The hub's reported auth mode (effective-identity feature): None
        # until the background probe (_fetch_hub_mode) resolves, so the idbar
        # renders its plain baseline immediately and never blocks first paint.
        self._hub_mode: str | None = None
        self._hub_unreachable = False

    def _idbar_text(self) -> str:
        login = self._creds.login if self._creds else None
        who = _idbar_who(login, self._hub_mode, unreachable=self._hub_unreachable)
        host = self._hub.split("//")[-1]
        stage_tag = "" if self._stage.name == "prod" else f" · stage:{self._stage.name}"
        return f" {who} · hub:{host}{stage_tag}"

    def compose(self) -> ComposeResult:
        self._idbar_prefix = self._idbar_text()
        yield Static(f"{self._idbar_prefix}   —   ↑↓←→ move · enter use · 1–3 jump · q quit",
                     id="idbar", classes="idbar")
        yield Tabs(*(Tab(label, id=f"tab-{key}") for key, label in _SECTIONS), id="nav")
        login = self._creds.login if self._creds else None
        with ContentSwitcher(initial=self._start, id="body"):
            yield HomeView(self._hub, login, id="home")
            yield EvolveForm(self._hub, self._creds, id="evolve")
            yield RunsView(id="runs")

    def on_mount(self) -> None:
        self.query_one("#nav", Tabs).active = f"tab-{self._start}"
        self.call_after_refresh(self._nav_start)  # dashboard nav is always ready underneath
        self._fetch_hub_mode()  # off-thread; repaints the idbar once it lands
        if self._evolve is not None:
            # auto-start the run + open its monitor over the dashboard; esc
            # detaches to the dashboard (Runs tab) with the run still going.
            plan = self._evolve
            self.call_after_refresh(lambda: self.start_run(plan))

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_hub_mode(self) -> None:
        # Same off-thread + call_from_thread pattern as HomeView._fetch_programs:
        # the idbar paints its baseline immediately (self._hub_mode starts
        # None) and this fills in OFFLINE/mismatch once the probe lands,
        # never blocking the dashboard's first paint on a slow/dead hub.
        try:
            mode = HubClient(self._hub, timeout=_HUB_MODE_TIMEOUT).hub_mode()
        except HubUnreachable:
            self.call_from_thread(self._apply_hub_mode, None, True)
            return
        self.call_from_thread(self._apply_hub_mode, mode, False)

    def _apply_hub_mode(self, mode: str | None, unreachable: bool) -> None:
        self._hub_mode = mode
        self._hub_unreachable = unreachable
        # A narrow idbar-only repaint (not the full _refresh_identity(), which
        # also cascades into HomeView.set_login -> another hub round-trip) --
        # the hub's mode doesn't change Home's own standing/login state.
        self._idbar_prefix = self._idbar_text()
        self._nav_update_hint()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Clicking a tab or moving with ← → (Textual's Tabs) switches the
        section. Guard on the event's *originating* ``Tabs`` -- only the main
        nav (``id="nav"``) may drive ``body.current`` -- so that any future
        nested ``Tabs`` inside a section can't misroute the ContentSwitcher to
        a child id that doesn't exist (which would raise ``NoMatches``)."""
        if event.tabs.id != "nav":
            return
        if not event.tab.id:
            return
        try:
            body = self.query_one("#body", ContentSwitcher)
        except Exception:
            return
        body.current = event.tab.id.removeprefix("tab-")
        if self._nav_mode == "navigate":
            # A MOUSE click on a tab focuses #nav -- and a focused Textual Tabs
            # eats ←/→ itself (moving its own active tab) instead of letting our
            # _nav_move drive them, so the gold cursor desynced from the section:
            # you'd "skip a tab" and could never arrow back to the clicked one.
            # Blur so the App's modal nav owns the arrows again. (Keyboard-driven
            # activation already runs with focus cleared, so this is a no-op
            # there.) Then keep the cursor on the tab that was actually activated,
            # so it never sits on a different tab than the shown section.
            self.set_focus(None)
            if self._nav_cursor is not None:
                self._nav_set_cursor(event.tab)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # The section-nav shortcuts (1/2/3 · e Evolve · l Login) belong to the
        # dashboard. While a screen is pushed on top (a run monitor, the login
        # modal), hide AND disable them -- they'd otherwise clutter that screen's
        # footer and, pressed there, silently switch the hidden dashboard section
        # underneath. The pushed screen keeps its own bindings (esc/s/q). `quit`
        # stays live everywhere. Returning False hides+disables; True shows.
        pushed = len(self.screen_stack) > 1
        return not (pushed and action in ("show", "evolve", "login"))

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
        self._nav_to_navigate()  # validates a possibly-stale cursor and re-shows it

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
        """Open the in-app GitHub device-flow login. On success the modal saves
        the credential and hands it back; we adopt it and refresh the chrome."""
        self.push_screen(LoginModal(), self._after_login)

    def _after_login(self, creds: Credentials | None) -> None:
        if creds is not None:
            self._creds = creds
            self._refresh_identity()

    def action_logout(self) -> None:
        from nethackers.hubclient import credentials
        credentials.clear()
        self._creds = None
        self._refresh_identity()

    def _refresh_identity(self) -> None:
        """Re-render the identity bar and Home after a login/logout."""
        self._idbar_prefix = self._idbar_text()
        self._nav_update_hint()  # repaints #idbar with the new prefix + current legend
        login = self._creds.login if self._creds else None
        with contextlib.suppress(Exception):
            self.query_one("#home", HomeView).set_login(login)
        with contextlib.suppress(Exception):
            self.query_one("#evolve", EvolveForm).set_creds(self._creds)

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
                "on_iteration": lambda it, res: self.call_from_thread(
                    self._on_run_iteration, run, it, res),
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

    def _on_run_iteration(self, run: Run, iteration: int, result: IterationResult) -> None:
        run.apply_iteration(iteration, result)
        m = self._monitor_for(run)
        if m is not None:
            m.render_iteration(iteration, result)

    def _finish_run(self, run: Run, results: object | None,
                    error: BaseException | None) -> None:
        run.finish(results=results, error=error)
        if error is not None:
            self.notify(f"run {run.rid} failed: {failure_detail(error)}",
                        severity="error", timeout=10, markup=False)
        m = self._monitor_for(run)
        if m is not None:
            m.render_state()

    def open_run(self, rid: str) -> None:
        run = self._runs.get(rid)
        if run is not None:
            self._show_monitor(run)

    def open_disk_run(self, run_dir: Path) -> None:
        """Reopen a run from an EARLIER session: rebuild a Run from its on-disk
        record (runs.reconstruct_run -- iterations, outcomes, scores and the
        mutator transcript) and show its monitor. Per-seed detail and
        per-identity Progress scores weren't persisted, so the monitor marks
        those 'not recorded'."""
        from nethackers.tui.screens.runs import reconstruct_run
        run = reconstruct_run(run_dir)
        if run is None:
            self.notify("couldn't read that run's saved record", severity="warning", timeout=4)
            return
        self._show_monitor(run)

    def _show_monitor(self, run: Run) -> None:
        scr = self.screen
        if isinstance(scr, RunMonitor):
            # a monitor is already up -> SWAP to this run's, never stack (else
            # opening a 2nd run buries the 1st and esc walks back through stale
            # monitors instead of returning to the dashboard).
            if scr.run is run:
                return  # already showing this run
            self.switch_screen(RunMonitor(run))
        else:
            self.push_screen(RunMonitor(run))

    def stop_run(self, rid: str) -> None:
        run = self._runs.get(rid)
        if run is not None:
            run.request_stop()

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
            "↑↓←→ move · enter use · 1–3 jump · q quit"
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
        tabs, plus the visible pane's controls / focusable cards."""
        targets: list[Widget] = list(self.query("#nav Tab"))
        try:
            body = self.query_one("#body", ContentSwitcher)
        except Exception:
            return dedup_visible(targets)
        current = body.current
        if current:
            pane = body.get_child_by_id(current)
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
            # the main tab row: left/right along the tabs, down dives in
            if direction in ("left", "right"):
                nxt = nearest_in_direction(cur, nav_tabs, direction)
            elif direction == "down":  # into the section body
                nxt = content[0] if content else None
            else:
                nxt = None  # already at the top
        else:
            nxt = nearest_in_direction(cur, content, direction)
            if nxt is None and direction == "up":  # leaving the top of the body
                nxt = self._active_section_tab()   # back up to the main tab
        if nxt is None:
            return
        self._nav_set_cursor(nxt)
        self._nav_switch_tab_live(nxt)

    def _nav_switch_tab_live(self, widget: Widget) -> None:
        """Moving the cursor onto a section tab switches to it live."""
        wid = widget.id or ""
        if wid.startswith("tab-"):
            self.query_one("#nav", Tabs).active = wid

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
            # A Select otherwise needs a second enter to open its list (focus,
            # then open). Navigating never opens it (the cursor isn't real
            # focus, so ↑↓ just move on); one enter here both focuses AND opens.
            if isinstance(w, Select):
                w.expanded = True
            self._nav_update_hint()

    def _nav_dive(self) -> None:
        for w in self._nav_targets():
            if not isinstance(w, Tab):
                self._nav_set_cursor(w)
                return

    def _nav_to_navigate(self) -> None:
        self._nav_mode = "navigate"
        self.set_focus(None)
        cursor = self._nav_cursor
        if cursor is None or cursor not in self._nav_targets():
            # the cursor's element vanished (e.g. a run button removed after the
            # run stopped) -> fall back to the active section's tab
            cursor = self._active_section_tab()
        if cursor is not None:
            self._nav_set_cursor(cursor)
        self._nav_update_hint()

    def on_key(self, event: events.Key) -> None:
        if len(self.screen_stack) > 1:
            return  # a screen is pushed (e.g. the evolve monitor) -- it owns its keys
        if self._nav_mode == "interact":
            if event.key == "escape":
                self._nav_to_navigate()
                event.stop()
                return
            # After you pick from a Select, its overlay closes but the Select
            # keeps focus -- so ↑↓ would REOPEN the list. Once it's closed,
            # treat an arrow as "done here": return to navigate and move the
            # cursor on. (While the overlay is open, focus is on the overlay,
            # not the Select, so this doesn't fire and ↑↓ walk the options.)
            focused = self.focused
            if (event.key in ("up", "down", "left", "right")
                    and isinstance(focused, Select) and not focused.expanded):
                self._nav_to_navigate()
                self._nav_move(event.key)
                event.stop()
                return
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
