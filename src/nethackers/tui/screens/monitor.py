"""The evolution monitor as a re-openable VIEW onto an app-owned ``Run``.

Opening it **backfills** the whole view from the run's accumulated state, then
the app forwards live worker events (``render_state``/``render_episode``/
``render_log``) while this screen is on top. ``esc`` detaches the view -- the
run keeps running -- and ``s`` stops it. Refactor of the old ``EvolveScreen``:
the per-run state now lives on the ``Run`` (``tui.run``) and the worker is
owned by the app, so the monitor holds only widgets."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Footer,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from nethackers.hubclient.live import episode_table
from nethackers.tui import status as S
from nethackers.tui._util import _slug
from nethackers.tui.art import tombstone
from nethackers.tui.run import Run

_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim"}


class RunMonitor(Screen):
    """PARENT -> CANDIDATE -> eval -> lineage cockpit + Monitor/Agent-log
    tabs + status line, rendered from a ``Run``. Holds no worker."""

    CSS = """
    RunMonitor #cockpit { height: auto; margin: 1 2 0 2; }
    RunMonitor #influences { color: #7c745f; }
    RunMonitor TabbedContent { width: 1fr; height: 1fr; margin: 0 2; }
    #tables { padding: 1 1; }
    #logs_list { width: 24; border-right: solid #d2a24c; }
    #logview { padding: 0 1; }
    """
    BINDINGS = [
        ("escape", "dismiss", "Back"),
        ("s", "stop", "Stop"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, run: Run) -> None:
        super().__init__()
        self.run = run
        self._batch_statics: list[Static] = []  # aligned with run.batches
        self._log_items: dict[str, str] = {}     # slug -> tag
        self._shown: dict[str, int] = {}          # tag -> #prettified lines already written

    def compose(self) -> ComposeResult:
        with Vertical(id="cockpit", classes="panel"):
            yield Static(id="parent")
            yield Static(id="candidate")
            yield Static("influences  —  (lights up when the loop selects them)",
                         id="influences")
            yield Static(id="eval")
            yield Static(id="lineage")
            yield Static(id="ledger")
        with TabbedContent():
            with TabPane("Monitor", id="tab_mon"):
                yield VerticalScroll(id="tables")
            with TabPane("Agent log", id="tab_logs"), Horizontal():
                yield ListView(id="logs_list")
                yield RichLog(id="logview", wrap=True, highlight=False, markup=False)
        yield Static(id="statusline", classes="statusline")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#cockpit").border_title = (
            f"⚔ Evolution · {self.run.cfg.objective} · {self.run.cfg.backend}")
        self._backfill()
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
        self.render_state()

    # ---- live renders (forwarded by the app while this screen is on top) ----
    def render_state(self) -> None:
        run, st = self.run, self.run.state
        self.query_one("#parent", Static).update(S.parent_panel(st))
        if st.get("phase") == "rejected":
            self.query_one("#candidate", Static).update(tombstone(
                [run.cfg.objective, f"iter {st['iteration']}", st["detail"] or "rejected"]))
        else:
            self.query_one("#candidate", Static).update(S.candidate_line(
                st, live_tokens=run.live_tokens(), elapsed_s=run.elapsed()))
        self.query_one("#eval", Static).update(
            S.eval_line(run.split(), run.eval_step, run.counts))
        self.query_one("#lineage", Static).update(S.lineage_strip(
            run.chain, best_dev=st["best_dev"], baseline_dev=st["baseline_dev"]))
        self.query_one("#ledger", Static).update(S.iterations_ledger(run.ledger_rows))
        self.query_one("#statusline", Static).update(S.status_line(
            run.cfg, st, live_tokens=run.live_tokens(), elapsed_s=run.elapsed()))

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
        if tag == self.run.sel_tag:
            self._write_new_log_lines(tag)
        self.render_state()

    # ---- agent-log list -----------------------------------------------------
    def _ensure_log_item(self, tag: str) -> None:
        slug = _slug(tag)
        if slug in self._log_items:
            return
        self._log_items[slug] = tag
        self.query_one("#logs_list", ListView).append(ListItem(Static(tag), id=slug))

    def _select_log(self, tag: str) -> None:
        self.run.sel_tag = tag
        self.query_one("#logview", RichLog).clear()
        self._shown[tag] = 0
        self._write_new_log_lines(tag)

    def _write_new_log_lines(self, tag: str) -> None:
        log = self.query_one("#logview", RichLog)
        lines = self.run.logs.get(tag, [])
        for kind, text in lines[self._shown.get(tag, 0):]:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._shown[tag] = len(lines)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        tag = self._log_items.get(event.item.id or "")
        if tag is not None:
            self._select_log(tag)

    def _tick(self) -> None:
        if self.run.state.get("phase") == "mutating":
            self.render_state()  # keep the elapsed clock live
