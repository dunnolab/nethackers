"""The evolution monitor as a re-openable VIEW onto an app-owned ``Run``.

Opening it **backfills** the whole view from the run's accumulated state, then
the app forwards live worker events (``render_state``/``render_episode``/
``render_log``) while this screen is on top. ``esc`` detaches the view -- the
run keeps running -- and ``s`` stops it. Refactor of the old ``EvolveScreen``:
the per-run state now lives on the ``Run`` (``tui.run``) and the worker is
owned by the app, so the monitor holds only widgets."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Footer,
    ListItem,
    ListView,
    RichLog,
    Static,
    Tab,
    TabbedContent,
    TabPane,
)

from nethackers.hubclient.live import episode_table
from nethackers.tui import status as S
from nethackers.tui._util import _slug
from nethackers.tui.art import tombstone
from nethackers.tui.nav import dedup_visible, nearest_in_direction
from nethackers.tui.run import Run

_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim",
               "brief": "#d2a24c"}  # the iteration's instruction, in the shell's gold


class RunMonitor(Screen):
    """PARENT -> CANDIDATE -> eval -> lineage cockpit + Monitor/Agent-log
    tabs + status line, rendered from a ``Run``. Holds no worker."""

    CSS = """
    RunMonitor #cockpit { height: auto; margin: 1 2 0 2; }
    RunMonitor #influences { color: #7c745f; }
    /* the per-identity scorecard (set objectives): bounded + scrollable so a
       large set never pushes the episode table off-screen; on a tall terminal
       it just shows the whole card. */
    RunMonitor #scorecard {
        height: auto; max-height: 50%; overflow-y: auto;
        margin: 0 1 1 1; color: #d7c9a2;
    }
    RunMonitor #navhint { color: #7c745f; height: 1; margin: 0 2; }
    RunMonitor TabbedContent { width: 1fr; height: 1fr; margin: 0 2; }
    #tables { padding: 1 1; }
    #logs_list { width: 24; }
    /* the highlighted iteration -- amber always (so which log you're viewing is
       clear even in navigate mode), brighter gold while the list is focused. */
    #logs_list > ListItem.-highlight { background: #d2a24c; color: #0b0b0e; text-style: bold; }
    #logs_list:focus > ListItem.-highlight { background: #ffd54a; }
    #logview {
        padding: 0 1; border: round #7c745f;
        border-title-color: #d2a24c; border-title-align: left;
    }
    /* the modal cursor: a gold chip on a tab, a gold ring (outline -> no
       reflow) on a content pane; :focus keeps the ring while interacting. */
    RunMonitor Tab.-cursor { background: #ffd54a; color: #0b0b0e; text-style: bold; }
    RunMonitor #tables.-cursor, RunMonitor #logs_list.-cursor,
    RunMonitor #logview.-cursor, RunMonitor #tables:focus,
    RunMonitor #logs_list:focus, RunMonitor #logview:focus {
        outline: heavy #ffd54a;
    }
    """
    BINDINGS = [
        ("escape", "nav_back", "Back"),
        ("c", "copy_log", "Copy log"),
        ("s", "stop", "Stop"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, run: Run) -> None:
        super().__init__()
        self.run = run
        self._batch_statics: list[Static] = []  # aligned with run.batches
        self._log_items: dict[str, str] = {}     # slug -> tag
        self._shown: dict[str, int] = {}          # tag -> #prettified lines already written
        # modal keyboard nav (mirrors the dashboard): "navigate" = arrows move a
        # visible cursor between the tabs + the active pane's controls; "interact"
        # = the cursor element holds real focus (scroll / pick).
        self._nav_mode = "navigate"
        self._nav_cursor: Widget | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="cockpit", classes="panel"):
            yield Static(id="parent")
            yield Static(id="candidate")
            yield Static("influences  —  (lights up when the loop selects them)",
                         id="influences")
            yield Static(id="eval")
            yield Static(id="lineage")
            yield Static(id="ledger")
        yield Static("↑↓←→ move · enter use · esc back · c copy log", id="navhint")
        with TabbedContent():
            with TabPane("Monitor", id="tab_mon"):
                yield Static(id="scorecard")
                yield VerticalScroll(id="tables")
            with TabPane("Agent log", id="tab_logs"), Horizontal():
                yield ListView(id="logs_list")
                yield RichLog(id="logview", wrap=True, highlight=False, markup=False)
        yield Static(id="statusline", classes="statusline")
        yield Footer()

    def on_mount(self) -> None:
        cfg = self.run.cfg
        pin = "".join([f" · {cfg.model}" if cfg.model else "",
                       f" · {cfg.effort}" if cfg.effort else ""])
        # a set run's identities may not be in state yet at mount (they arrive
        # with the first apply_state) -- append the size when already known,
        # else leave the plain title; the scorecard is the primary signal.
        n = len(self.run.identities())
        setn = f" ({n})" if n else ""
        self.query_one("#cockpit").border_title = (
            f"⚔ Evolution · {cfg.objective}{setn} · {cfg.backend}{pin}")
        # defer: the TabbedContent's panes (#tables/#logs_list/#logview) aren't
        # mounted yet during a Screen's on_mount, so backfill would NoMatches.
        self.call_after_refresh(self._backfill)
        # then start nav (after Textual's own initial auto-focus too)
        self.call_after_refresh(self._nav_start)
        self.set_interval(1.0, self._tick)

    def action_stop(self) -> None:
        self.run.stop.set()
        self.app.notify(f"stopping run {self.run.rid} …", timeout=4)

    # ---- backfill (on open) -------------------------------------------------
    def _backfill(self) -> None:
        for tag in self.run.logs:
            self._ensure_log_item(tag)
        scroll = self.query_one("#tables", VerticalScroll)
        for batch in self.run.batches:
            static = Static()
            scroll.mount(static)
            self._batch_statics.append(static)
            static.update(episode_table(batch.label, batch.rows(), done=batch.done))
        if self.run.sel_tag:
            self._select_log(self.run.sel_tag)
        # highlight the shown iteration in the list once its items have mounted
        self.call_after_refresh(self._highlight_current)
        self.render_state()

    # ---- live renders (forwarded by the app while this screen is on top) ----
    def render_state(self) -> None:
        run, st = self.run, self.run.state
        # No next batch arrives to seal the final table. Run.finish marks it
        # done, and the worker's final render_state call must refresh its
        # caption from "running… n/n" to "✓ complete".
        if not run.running and self._batch_statics:
            batch = run.current_batch()
            if batch is not None:
                self._batch_statics[-1].update(
                    episode_table(batch.label, batch.rows(), done=batch.done))
        self.query_one("#parent", Static).update(S.parent_panel(st))
        if st.get("phase") == "rejected":
            self.query_one("#candidate", Static).update(tombstone(
                [run.cfg.objective, f"iter {st['iteration']}", st["detail"] or "rejected"]))
        else:
            # live streaming tokens while mutating; once past it (gating/eval)
            # fall back to the iteration's authoritative op total so the panel
            # doesn't drop back to 0 mid-iteration (codex only reports at the end).
            self.query_one("#candidate", Static).update(S.candidate_line(
                st, live_tokens=run.live_tokens() or st.get("tokens", 0),
                elapsed_s=run.elapsed()))
        self.query_one("#eval", Static).update(
            S.eval_line(run.split(), run.eval_step, run.counts))
        self.query_one("#lineage", Static).update(S.lineage_strip(
            run.chain, best_dev=st["best_dev"], baseline_dev=st["baseline_dev"]))
        self.query_one("#ledger", Static).update(S.iterations_ledger(run.ledger_rows))
        # generalist (set) objectives only: the loop puts "identities" in
        # state, so this stays a no-op for single/random runs and their
        # #scorecard/#influences are left as-is (unused, hidden by height:auto).
        idents = run.identities()
        if idents:
            # candidate_means only during evaluating-dev -- other phases would
            # show a stale or wrong-split (held) breakdown against the dev-only
            # parent_means, which a reviewer flagged as misleading.
            cand = run.candidate_means() if st.get("phase") == "evaluating-dev" else None
            self.query_one("#scorecard", Static).update(
                S.scorecard(run.parent_means(), cand, idents))
            cov = st.get("coverage")
            pd = str(st.get("parent_digest", ""))[:4]
            infl = (f"influence  #{pd} · covers {cov[0]}/{cov[1]}" if (pd and cov)
                    else "influence  seed · cold-start")
            self.query_one("#influences", Static).update(infl)
        # the bottom status bar is the run's persistent gauge: cumulative tokens
        # (across every iteration) and total run time -- both always advancing.
        self.query_one("#statusline", Static).update(S.status_line(
            run.cfg, st, live_tokens=run.total_tokens(), elapsed_s=run.run_time()))

    def render_episode(self, label: str, ep: dict) -> None:
        scroll = self.query_one("#tables", VerticalScroll)
        # mount a Static for each run.batch the view hasn't caught up to,
        # finalizing the previously-current one as it's superseded.
        while len(self._batch_statics) < len(self.run.batches):
            if self._batch_statics:
                prev = self.run.batches[len(self._batch_statics) - 1]
                self._batch_statics[-1].update(
                    episode_table(prev.label, prev.rows(), done=True))
            static = Static()
            scroll.mount(static)
            self._batch_statics.append(static)
        cur = self.run.current_batch()
        if cur is not None and self._batch_statics:
            self._batch_statics[-1].update(episode_table(cur.label, cur.rows(), done=cur.done))
        scroll.scroll_end(animate=False)
        self.render_state()

    def render_log(self, tag: str) -> None:
        self._ensure_log_item(tag)
        logview = self.query_one("#logview", RichLog)
        if str(logview.border_title or "") != (self.run.sel_tag or ""):
            # the shown iteration changed (a new one started, or a fresh pick) ->
            # switch the view cleanly (retitle + rewrite) and move the highlight,
            # so lines never accumulate under the wrong iteration label.
            if self.run.sel_tag:
                self._select_log(self.run.sel_tag)
                self.call_after_refresh(self._highlight_current)
        elif tag == self.run.sel_tag:
            self._write_new_log_lines(tag)  # same iteration -> append new lines
        self.render_state()

    # ---- agent-log list -----------------------------------------------------
    def _ensure_log_item(self, tag: str) -> None:
        slug = _slug(tag)
        if slug in self._log_items:
            return
        try:
            lv = self.query_one("#logs_list", ListView)
        except NoMatches:
            return  # list not mounted yet; re-ensured on the next render_log
        self._log_items[slug] = tag
        lv.append(ListItem(Static(tag), id=slug))

    def _select_log(self, tag: str) -> None:
        self.run.sel_tag = tag
        logview = self.query_one("#logview", RichLog)
        logview.border_title = tag  # label WHICH iteration this log belongs to
        logview.clear()
        self._shown[tag] = 0
        self._write_new_log_lines(tag)

    def _highlight_current(self) -> None:
        """Highlight the shown iteration in the list (default: the latest) so
        it's obvious which iteration's log is on the right."""
        try:
            lv = self.query_one("#logs_list", ListView)
        except NoMatches:
            return  # a deferred (call_after_refresh) call landed after dismiss

        target = _slug(self.run.sel_tag) if self.run.sel_tag else None
        for i, item in enumerate(lv.children):
            if item.id == target:
                lv.index = i
                return
        if lv.children:  # no selection yet -> the most recent iteration
            lv.index = len(lv.children) - 1

    def _write_new_log_lines(self, tag: str) -> None:
        log = self.query_one("#logview", RichLog)
        lines = self.run.logs.get(tag, [])
        for kind, text in lines[self._shown.get(tag, 0):]:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._shown[tag] = len(lines)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # arrowing the list live-swaps the log on the right -- no Enter needed,
        # so navigating iterations *is* navigating their logs.
        item = event.item
        tag = self._log_items.get(item.id or "") if item is not None else None
        if tag is not None and tag != self.run.sel_tag:
            self._select_log(tag)

    def _tick(self) -> None:
        # always re-render: the status bar's run-time clock + cumulative tokens
        # advance even between phases (not just while mutating).
        self.render_state()

    # ---- modal keyboard navigation (mirrors the dashboard) ------------------
    def _nav_targets(self) -> list[Widget]:
        """The tabs, plus the active pane's focusable controls."""
        tabs: list[Widget] = list(self.query(Tab))
        active = self.query_one(TabbedContent).active
        if active == "tab_mon":
            content = list(self.query("#tables"))
        else:
            content = list(self.query("#logs_list")) + list(self.query("#logview"))
        return dedup_visible(tabs + content)

    def _active_tab_widget(self) -> Widget | None:
        active = self.query_one(TabbedContent).active
        for tab in self.query(Tab):
            if (tab.id or "").removeprefix("--content-tab-") == active:
                return tab
        return None

    def _nav_set_cursor(self, widget: Widget) -> None:
        if self._nav_cursor is not None:
            self._nav_cursor.remove_class("-cursor")
        self._nav_cursor = widget
        widget.add_class("-cursor")
        widget.scroll_visible()

    def _nav_start(self) -> None:
        try:
            targets = self._nav_targets()  # queries -> NoMatches if already dismissed
        except NoMatches:
            return  # deferred from on_mount; the monitor may already be dismissed
        self._nav_mode = "navigate"
        self.set_focus(None)  # navigate mode: nothing focused, so on_key gets arrows
        if targets:
            self._nav_set_cursor(targets[0])

    def _nav_move(self, direction: str) -> None:
        cur = self._nav_cursor
        if cur is None:
            self._nav_start()
            return
        others = [w for w in self._nav_targets() if w is not cur]
        tabs = [w for w in others if isinstance(w, Tab)]
        content = [w for w in others if not isinstance(w, Tab)]
        if isinstance(cur, Tab):
            if direction in ("left", "right"):
                nxt = nearest_in_direction(cur, tabs, direction)
            elif direction == "down":
                nxt = content[0] if content else None  # dive into the pane
            else:
                nxt = None
        else:
            nxt = nearest_in_direction(cur, content, direction)
            if nxt is None and direction == "up":  # back up to this pane's tab
                nxt = self._active_tab_widget()
        if nxt is None:
            return
        self._nav_set_cursor(nxt)
        if isinstance(nxt, Tab) and nxt.id:  # landing on a tab switches to it live
            self.query_one(TabbedContent).active = nxt.id.removeprefix("--content-tab-")

    def _nav_activate(self) -> None:
        w = self._nav_cursor
        if w is None:
            return
        if isinstance(w, Tab):  # dive into the (now-active) pane's first control
            for t in self._nav_targets():
                if not isinstance(t, Tab):
                    self._nav_set_cursor(t)
                    return
        else:  # a scroll / list -> take real focus so arrows scroll / pick
            self._nav_mode = "interact"
            w.focus()

    def _nav_to_navigate(self) -> None:
        self._nav_mode = "navigate"
        self.set_focus(None)
        if self._nav_cursor is not None:
            self._nav_cursor.add_class("-cursor")

    def on_key(self, event: events.Key) -> None:
        if self._nav_mode == "interact":
            return  # the focused control owns keys; `esc` returns via action_nav_back
        if event.key in ("up", "down", "left", "right"):
            self._nav_move(event.key)
            event.stop()
        elif event.key == "enter":
            self._nav_activate()
            event.stop()

    def action_nav_back(self) -> None:
        # interact -> back to navigate; navigate -> leave the monitor (run stays).
        if self._nav_mode == "interact":
            self._nav_to_navigate()
        else:
            self.dismiss()
            # the dashboard resumes with #nav focused (Textual restores it) --
            # tell it to reclaim navigate mode so ←/→ don't get eaten by Tabs.
            reassert = getattr(self.app, "reassert_navigate", None)
            if callable(reassert):
                self.app.call_after_refresh(reassert)

    def action_copy_log(self) -> None:
        lines = self.run.logs.get(self.run.sel_tag or "", [])
        text = "\n".join(t for _kind, t in lines)
        if text:
            self.app.copy_to_clipboard(text)
            self.app.notify(f"copied {len(lines)} log lines to clipboard", timeout=3)
        else:
            self.app.notify("no log lines to copy", severity="warning", timeout=3)
