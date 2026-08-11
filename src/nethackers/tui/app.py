"""Textual UI for `nethackers evolve`: a pinned status bar over two tabs --
the per-seed episode Tables and the coding agent's per-iteration Mutation
logs. The synchronous run_loop runs in a worker thread and feeds the UI via
callbacks handed off with call_from_thread."""
from __future__ import annotations

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

from nethackers.harness.operator import agent_tokens
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
    BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

    def __init__(self, cfg: EvolveConfig, run: Callable[[dict], object] | None = None) -> None:
        super().__init__()
        self._cfg = cfg
        self._run = run
        self.results: object | None = None
        self._state: dict = dict(_INITIAL)
        self._title = f"evolving {cfg.objective} · {cfg.backend}"
        self._cur_label: str | None = None
        self._cur_rows: list[dict] = []
        self._cur_static: Static | None = None
        self._eval_step: tuple[int, int, float] | None = None
        self._logs: dict[str, list[tuple[str, str]]] = {}
        self._items: dict[str, str] = {}  # slug -> tag
        self._live_tokens: dict[str, int] = {}
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

    @work(thread=True)
    def _worker(self) -> None:
        assert self._run is not None
        self.results = self._run({
            "on_state": lambda s: self.call_from_thread(self._apply_state, s),
            "on_episode": lambda label, ep: self.call_from_thread(self._apply_episode, label, ep),
            "on_log": lambda tag, line: self.call_from_thread(self._apply_log, tag, line),
        })

    # ---- handlers (app thread) ----
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

    def _apply_episode(self, label: str, ep: dict) -> None:
        scroll = self.query_one("#tables", VerticalScroll)
        if label != self._cur_label:
            if self._cur_static is not None and self._cur_rows and self._cur_label is not None:
                self._cur_static.update(episode_table(self._cur_label, self._cur_rows, done=True))
            self._cur_label = label
            self._cur_rows = []
            self._cur_static = Static()
            scroll.mount(self._cur_static)
        self._cur_rows.append(ep)
        if self._cur_static is not None:
            self._cur_static.update(episode_table(label, self._cur_rows, done=False))
        scroll.scroll_end(animate=False)
        mean = sum(float(r["progress"]) for r in self._cur_rows) / len(self._cur_rows)
        self._eval_step = (int(ep["index"]), int(ep["total"]), mean)
        self._refresh_status()

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
        live = self._live_tokens.get(self._running_tag(), 0)
        line1, line2 = format_status(self._cfg, self._state, live_tokens=live,
                                     eval_step=self._eval_step, elapsed_s=self._elapsed())
        self.query_one("#status", Static).update(f"[b]{self._title}[/]\n{line1}\n{line2}")
