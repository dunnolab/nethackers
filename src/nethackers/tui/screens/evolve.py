"""Textual Screen for `nethackers evolve`: the live evolution monitor Screen
hosted by NetHackersApp -- PARENT -> CANDIDATE -> eval -> lineage, with a
status-line motif and a RIP tombstone on rejection. Reworked from the old
standalone tui.app.EvolveApp (since deleted): the worker/call_from_thread
hand-off, @_guarded handlers, and the per-iteration agent-log pane
(ListView + RichLog) are ported mechanics; the status now renders through
the status.py monitor formatters instead of the old two-line format_status
bar.

Screen (unlike App) has neither .call_from_thread() nor .exit() of its own
-- both live only on App -- so the worker below reaches through self.app
for those two calls. Worker registration (`@work`) and `self.log` are
shared DOMNode/MessagePump machinery and work the same on a Screen.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from rich.text import Text
from textual import work
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

from nethackers.harness.operator import agent_tokens
from nethackers.hubclient.live import episode_table
from nethackers.tui import status as S
from nethackers.tui._util import _guarded, _rows_in_order, _slug
from nethackers.tui.art import tombstone
from nethackers.tui.prettify import prettify
from nethackers.tui.status import EvolveConfig

_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim"}
_INITIAL_STATE: dict = {
    "phase": "cold-start", "iteration": 0, "baseline_dev": 0.0, "baseline_held": 0.0,
    "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
    "parent_digest": "", "parent_dev": 0.0, "parent_held": 0.0, "generation": 0,
}


class EvolveScreen(Screen):
    """The evolution monitor: PARENT -> CANDIDATE -> eval -> lineage, live,
    over a Monitor tab (per-batch episode tables) and an Agent log tab
    (per-iteration mutation stream). ``escape`` dismisses back to whatever
    sits beneath in the screen stack -- the dashboard, for both the
    ``⚔ Evolve`` tab's in-app launch (Task 16, ``EvolveForm.push_screen``)
    and the CLI's ``nethackers evolve`` TTY path (``NetHackersApp.on_mount``
    pushes this screen over the same dashboard ``compose()`` already
    built)."""

    CSS = """
    EvolveScreen #cockpit { height: auto; margin: 1 2 0 2; }
    EvolveScreen #influences { color: #7c745f; }
    EvolveScreen TabbedContent { width: 1fr; height: 1fr; margin: 0 2; }
    #tables { padding: 1 1; }
    #logs_list { width: 24; border-right: solid #d2a24c; }
    #logview { padding: 0 1; }
    """
    BINDINGS = [("q", "app.quit", "Quit"), ("escape", "dismiss", "Back")]

    def __init__(
        self,
        cfg: EvolveConfig,
        run: Callable[[dict], object] | None = None,
        exit_on_error: bool = False,
    ) -> None:
        super().__init__()
        self._cfg = cfg
        self._run = run
        self._exit_on_error = exit_on_error
        self.results: object | None = None
        self.error: BaseException | None = None
        self._state: dict = dict(_INITIAL_STATE)
        self._chain: list[str] = []
        self._ledger_rows: list[tuple[int, bool, str]] = []
        self._counts: dict[str, int] = {}
        self._cur_label: str | None = None
        self._cur_rows_by_index: dict[int, dict] = {}
        self._cur_static: Static | None = None
        self._eval_step: tuple[int, int, float] | None = None
        self._logs: dict[str, list[tuple[str, str]]] = {}
        self._items: dict[str, str] = {}  # slug -> tag
        self._live_tokens: dict[str, int] = {}
        self._sel_tag: str | None = None
        self._mut_start = 0.0

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
            f"⚔ Evolution · {self._cfg.objective} · {self._cfg.backend}")
        self._refresh()
        self.set_interval(1.0, self._tick)
        if self._run is not None:
            self._worker()

    @work(thread=True, exit_on_error=False)
    def _worker(self) -> None:
        assert self._run is not None
        try:
            self.results = self._run({
                "on_state": lambda s: self.app.call_from_thread(self._apply_state, s),
                "on_episode": lambda label, ep: self.app.call_from_thread(
                    self._apply_episode, label, ep),
                "on_log": lambda tag, line: self.app.call_from_thread(self._apply_log, tag, line),
            })
        except Exception as exc:
            self.error = exc
            if self._exit_on_error:
                # CLI path (NetHackersApp's evolve= construction): tear the
                # whole app down so cli.py's app.run()/app.error re-raise
                # contract (main()'s friendly hub/docker handlers) fires.
                self.app.call_from_thread(self.app.exit)
            else:
                # In-app path (EvolveForm's Start button): the dashboard
                # stays up -- surface the failure and fall back to it
                # instead of silently killing the whole shell.
                self.app.call_from_thread(self._fatal)

    def _fatal(self) -> None:
        """Off the worker thread, via call_from_thread: notify + dismiss
        back to the dashboard on a cold-start failure, when this screen
        isn't the CLI's exit_on_error=True instance."""
        self.app.notify(f"evolve failed: {self.error}", severity="error", timeout=10)
        self.dismiss()

    # ---- handlers (app thread) ----
    @_guarded
    def _apply_state(self, state: dict) -> None:
        prev = self._state.get("phase")
        self._state = state
        phase = state["phase"]
        if phase == "mutating":
            self._mut_start = time.monotonic()
            tag = self._tag(state["iteration"])
            self._ensure_log(tag)
            self._select_log(tag)
        elif phase in ("evaluating-dev", "evaluating-held") and prev != phase:
            self._eval_step = None

        # R-chain: the lineage chain is built from on_state alone -- append
        # the parent snapshot whenever it differs from the chain's tail
        # (seed -> elite1 -> elite2 ...). The in-flight child's digest isn't
        # in on_state, so lineage_strip's trailing "-> ?" covers it.
        parent_digest = state.get("parent_digest")
        if parent_digest and (not self._chain or self._chain[-1] != parent_digest):
            self._chain.append(parent_digest)

        if phase == "registered":
            self._ledger_rows.append((state["iteration"], True, "registered"))
        elif phase == "rejected":
            detail = state["detail"] or "rejected"
            self._ledger_rows.append((state["iteration"], False, detail))
            # The RIP tombstone owns #candidate on rejection; _refresh()
            # skips candidate_line() while phase == "rejected" so it isn't
            # immediately overwritten below.
            self.query_one("#candidate", Static).update(tombstone(
                [self._cfg.objective, f"iter {state['iteration']}", detail]))
        self._refresh()

    @_guarded
    def _apply_episode(self, label: str, ep: dict) -> None:
        scroll = self.query_one("#tables", VerticalScroll)
        if label != self._cur_label:
            if (
                self._cur_static is not None
                and self._cur_rows_by_index
                and self._cur_label is not None
            ):
                self._cur_static.update(episode_table(
                    self._cur_label, _rows_in_order(self._cur_rows_by_index), done=True))
            self._cur_label = label
            self._cur_rows_by_index = {}
            self._cur_static = Static()
            scroll.mount(self._cur_static)
            self._counts = {}
        # Keyed by index (== batch/seed position), not append order: parallel
        # eval (M3) means episodes can complete out of order, but the table
        # should always read left-to-right in batch order.
        self._cur_rows_by_index[int(ep["index"])] = ep
        self._counts[ep["status"]] = self._counts.get(ep["status"], 0) + 1
        rows = _rows_in_order(self._cur_rows_by_index)
        if self._cur_static is not None:
            self._cur_static.update(episode_table(label, rows, done=False))
        scroll.scroll_end(animate=False)
        mean = sum(float(r["progress"]) for r in rows) / len(rows)
        # done/total (how many of this batch's episodes have finished), not
        # the arriving episode's own index -- that's no longer monotonic
        # once episodes complete out of order.
        self._eval_step = (len(rows), int(ep["total"]), mean)
        self._refresh()

    @_guarded
    def _apply_log(self, tag: str, line: str) -> None:
        self._ensure_log(tag)
        pretty = prettify(self._cfg.backend, line)
        self._logs.setdefault(tag, []).extend(pretty)
        gained = agent_tokens(self._cfg.backend, line)
        self._live_tokens[tag] = self._live_tokens.get(tag, 0) + gained
        if tag == self._sel_tag and pretty:
            log = self.query_one("#logview", RichLog)
            for kind, text in pretty:
                log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._refresh()

    # ---- agent-log list (per-iteration tabs within the Agent log pane) ----
    def _tag(self, iteration: int) -> str:
        return f"iter {iteration}/{self._cfg.iterations}"

    def _ensure_log(self, tag: str) -> None:
        if tag in self._logs:
            return
        self._logs[tag] = []
        slug = _slug(tag)
        self._items[slug] = tag
        self.query_one("#logs_list", ListView).append(ListItem(Static(tag), id=slug))

    def _select_log(self, tag: str) -> None:
        self._sel_tag = tag
        log = self.query_one("#logview", RichLog)
        log.clear()
        for kind, text in self._logs.get(tag, []):
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        tag = self._items.get(event.item.id or "")
        if tag is not None:
            self._select_log(tag)

    # ---- status ----
    def _running_tag(self) -> str:
        if self._state.get("phase") == "mutating":
            return self._tag(self._state["iteration"])
        return ""

    def _elapsed(self) -> float:
        if self._state.get("phase") == "mutating":
            return time.monotonic() - self._mut_start
        return 0.0

    def _tick(self) -> None:
        if self._state.get("phase") == "mutating":
            self._refresh()

    def _split(self) -> str:
        """'dev'/'held', derived from the current phase, falling back to the
        current episode batch label (e.g. "iter 1/3 · held-out") for phases
        (mutating/gating/registered/...) that don't name a split directly."""
        phase = str(self._state.get("phase", ""))
        if phase.endswith("dev"):
            return "dev"
        if phase.endswith("held"):
            return "held"
        return "held" if "held" in (self._cur_label or "") else "dev"

    def _refresh(self) -> None:
        st = self._state
        live = self._live_tokens.get(self._running_tag(), 0)
        elapsed = self._elapsed()
        self.query_one("#parent", Static).update(S.parent_panel(st))
        if st.get("phase") != "rejected":
            self.query_one("#candidate", Static).update(S.candidate_line(
                st, live_tokens=live, token_budget=self._cfg.token_budget, elapsed_s=elapsed))
        self.query_one("#eval", Static).update(
            S.eval_line(self._split(), self._eval_step, self._counts))
        self.query_one("#lineage", Static).update(S.lineage_strip(
            self._chain, best_dev=st["best_dev"], baseline_dev=st["baseline_dev"]))
        self.query_one("#ledger", Static).update(S.iterations_ledger(self._ledger_rows))
        self.query_one("#statusline", Static).update(S.status_line(
            self._cfg, st, live_tokens=live, elapsed_s=elapsed))
