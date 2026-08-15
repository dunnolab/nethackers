"""Textual UI for `nethackers evolve`: a pinned status bar over two tabs --
the per-seed episode Tables and the coding agent's per-iteration Mutation
logs. The synchronous run_loop runs in a worker thread and feeds the UI via
callbacks handed off with call_from_thread."""
from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Footer,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from nethackers.harness.metering import Meter
from nethackers.hubclient.live import episode_table
from nethackers.tui.prettify import prettify
from nethackers.tui.status import EvolveConfig, format_status

_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim"}
_INITIAL = {
    "phase": "cold-start", "iteration": 0, "baseline_dev": 0.0, "baseline_held": 0.0,
    "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
}


def _slug(tag: str) -> str:
    return "log_" + tag.replace(" ", "_").replace("/", "_")


def _rows_in_order(rows_by_index: dict[int, dict]) -> list[dict]:
    """Render a batch's episode rows in index (== batch/seed) order,
    regardless of the order they actually completed in -- parallel eval
    (M3) means episode k+1 can finish before episode k."""
    return [rows_by_index[k] for k in sorted(rows_by_index)]


def _guarded(method):
    """Per the M3 evolve TUI design spec's Errors section: "a callback
    raising is caught and dropped (logged to a debug buffer, never
    surfaced)". Without this, an exception raised inside a handler body
    propagates through call_from_thread's future.result() back into the
    worker thread -- aborting the whole evolve run over a display concern
    (malformed episode/state dict, bad log line), including at the
    cold-start/done call sites in run_loop that sit outside its
    per-iteration try/except."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self.log.error(f"display handler {method.__name__} raised: {exc!r}; dropped")
            return None
    return wrapper


class EvolveApp(App):
    CSS = """
    #status { dock: top; height: 3; padding: 0 1; background: $panel; color: $text; }
    #body { align: center top; }
    TabbedContent { width: 1fr; max-width: 140; }
    #tables { padding: 1 2; }
    #logrow { height: 1fr; }
    #logs_list { width: 20; border-right: solid $accent; }
    #logview { padding: 0 1; }
    """
    BINDINGS = [("q", "stop_and_quit", "Quit"), ("ctrl+c", "stop_and_quit", "Quit")]

    def __init__(self, cfg: EvolveConfig, run: Callable[[dict], object] | None = None) -> None:
        super().__init__()
        self._cfg = cfg
        self._run = run
        self.results: object | None = None
        self.error: BaseException | None = None
        self._state: dict = dict(_INITIAL)
        self._title = f"evolving {cfg.objective} · {cfg.backend}"
        self._cur_label: str | None = None
        self._cur_rows_by_index: dict[int, dict] = {}
        self._cur_static: Static | None = None
        self._eval_step: tuple[int, int, float] | None = None
        self._logs: dict[str, list[tuple[str, str]]] = {}
        self._items: dict[str, str] = {}  # slug -> tag
        self._meters: dict[str, Meter] = {}
        self._stop = threading.Event()
        self._sel_tag: str | None = None
        self._mut_start = 0.0

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        with Horizontal(id="body"), TabbedContent():
            with TabPane("Tables", id="tab_tables"):
                yield VerticalScroll(id="tables")
            with TabPane("Mutation logs", id="tab_logs"), Horizontal(id="logrow"):
                yield ListView(id="logs_list")
                yield RichLog(id="logview", wrap=True, highlight=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        self.set_interval(1.0, self._tick)
        if self._run is not None:
            self._worker()

    def action_stop_and_quit(self) -> None:
        self._stop.set()  # hard-kill the running operator subprocess group (no orphan)
        self.exit()

    @work(thread=True, exit_on_error=False)
    def _worker(self) -> None:
        assert self._run is not None
        try:
            self.results = self._run({
                "on_state": lambda s: self.call_from_thread(self._apply_state, s),
                "on_episode": lambda label, ep: self.call_from_thread(
                    self._apply_episode, label, ep),
                "on_log": lambda tag, line: self.call_from_thread(self._apply_log, tag, line),
                "stop": self._stop,
            })
        except Exception as exc:
            self.error = exc
            self.call_from_thread(self.exit)

    # ---- handlers (app thread) ----
    @_guarded
    def _apply_state(self, state: dict) -> None:
        prev = self._state.get("phase")
        self._state = state
        if state["phase"] == "mutating":
            self._mut_start = time.monotonic()
            tag = self._tag(state["iteration"])
            self._ensure_log(tag)
            self._select_log(tag)
        elif state["phase"] in ("evaluating-dev", "evaluating-held") and prev != state["phase"]:
            self._eval_step = None
        self._refresh_status()

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
        # Keyed by index (== batch/seed position), not append order: parallel
        # eval (M3) means episodes can complete out of order, but the table
        # should always read left-to-right in batch order.
        self._cur_rows_by_index[int(ep["index"])] = ep
        rows = _rows_in_order(self._cur_rows_by_index)
        if self._cur_static is not None:
            self._cur_static.update(episode_table(label, rows, done=False))
        scroll.scroll_end(animate=False)
        mean = sum(float(r["progress"]) for r in rows) / len(rows)
        # done/total (how many of this batch's episodes have finished), not
        # the arriving episode's own index -- that's no longer monotonic
        # once episodes complete out of order.
        self._eval_step = (len(rows), int(ep["total"]), mean)
        self._refresh_status()

    @_guarded
    def _apply_log(self, tag: str, line: str) -> None:
        self._ensure_log(tag)
        pretty = prettify(self._cfg.backend, line)
        self._logs.setdefault(tag, []).extend(pretty)
        self._meters.setdefault(tag, Meter(self._cfg.backend)).observe(line)
        if tag == self._sel_tag and pretty:
            log = self.query_one("#logview", RichLog)
            for kind, text in pretty:
                log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._refresh_status()

    # ---- logs list ----
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
        return time.monotonic() - self._mut_start if self._state.get("phase") == "mutating" else 0.0

    def _tick(self) -> None:
        if self._state.get("phase") == "mutating":
            self._refresh_status()

    def _refresh_status(self) -> None:
        meter = self._meters.get(self._running_tag())
        live = meter.usage.total if meter is not None else 0
        line1, line2 = format_status(self._cfg, self._state, live_tokens=live,
                                     eval_step=self._eval_step, elapsed_s=self._elapsed())
        self.query_one("#status", Static).update(f"[b]{self._title}[/]\n{line1}\n{line2}")
