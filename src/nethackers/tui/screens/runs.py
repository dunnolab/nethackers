"""The Runs hub: ongoing runs (live, from the app's Run registry) on top --
select one to jump into its monitor -- over past runs read from disk."""
from __future__ import annotations

import contextlib
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Static

from nethackers.config import load_stage
from nethackers.tui.status import _clock, _compact

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp


def _summarize(run_dir: Path) -> dict | None:
    try:
        cfg = json.loads((run_dir / "run.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    wins, best_dev, tokens = 0, None, 0
    causes: Counter[str] = Counter()
    mfile = run_dir / "metrics.jsonl"
    if mfile.exists():
        for line in mfile.read_text().splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            tokens += int(m.get("tokens") or 0)  # operator tokens for this iteration
            for cause, n in (m.get("causes") or {}).items():
                causes[cause] += int(n)
            # "local-only" (runlog.metric_record) is still a real, accepted
            # local elite -- it only failed to reach the hub -- so it counts
            # as a win here exactly like "registered" does.
            if m.get("outcome") in ("registered", "local-only"):
                wins += 1
                if m.get("dev_fitness") is not None:
                    best_dev = m["dev_fitness"]
    return {
        "run_id": cfg.get("run_id", run_dir.name),
        "objective": cfg.get("objective", ""),
        "operator": cfg.get("operator", ""),
        "created_at": cfg.get("created_at", ""),
        "iterations": cfg.get("iterations", 0),
        "wins": wins,
        "best_dev": best_dev,
        "tokens": tokens,
        "causes": dict(causes),
    }


def read_runs(runs_dir: Path) -> list[dict]:
    if not runs_dir.exists():
        return []
    out = []
    for child in runs_dir.iterdir():
        if child.is_symlink() or not child.is_dir():  # skip the `latest` symlink
            continue
        summary = _summarize(child)
        if summary is not None:
            out.append(summary)
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def run_totals(runs: list[dict]) -> dict:
    """Aggregate the local evolve runs for the Home summary: how many runs,
    total accepted wins, and total operator tokens spent across them all."""
    return {
        "runs": len(runs),
        "wins": sum(int(r.get("wins", 0)) for r in runs),
        "iterations": sum(int(r.get("iterations", 0)) for r in runs),
        "tokens": sum(int(r.get("tokens", 0)) for r in runs),
    }


def run_causes(runs: list[dict]) -> dict[str, int]:
    """Sum every local run's per-run cause counts into one cause -> count map
    for the Home 'Causes of Death' panel."""
    total: Counter[str] = Counter()
    for r in runs:
        for cause, n in (r.get("causes") or {}).items():
            total[cause] += int(n)
    return dict(total)


class RunsView(VerticalScroll):
    """This session's runs (live + finished) as a pick-to-open list -- each
    reopens its full monitor with all the detail it recorded -- then older runs
    from earlier sessions below as a read-only summary (their live per-seed
    detail isn't persisted, so they can't be reopened into the monitor)."""

    DEFAULT_CSS = """
    RunsView { margin: 1 2; padding: 0 1; height: 1fr; }
    RunsView #runs_ongoing_title { color: #d2a24c; text-style: bold; }
    RunsView #runs_ongoing { height: auto; margin-bottom: 1; }
    RunsView .ongoing-run {
        width: 1fr; height: 3; margin: 0 0 1 0;
        border: round #d2a24c; background: #16161c; color: #d7c9a2;
        text-align: left; content-align: left middle; text-style: none;
    }
    RunsView .ongoing-run:hover { background: #20202b; }
    RunsView #runs_past_title { color: #7c745f; margin-top: 1; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.add_class("panel")
        self._ongoing_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(id="runs_ongoing_title")
        yield Vertical(id="runs_ongoing")  # one focusable Button per ongoing run
        yield Static("earlier sessions · summary only", id="runs_past_title")
        yield Static(id="runs_past")

    def on_mount(self) -> None:
        self.border_title = "▶ Runs"
        self._refresh()
        self.set_interval(1.0, self._tick)

    def on_show(self) -> None:
        self._refresh()

    def _tick(self) -> None:
        if self.display:  # only while the Runs section is the visible pane
            self._refresh_session()

    def _refresh(self) -> None:
        self._refresh_session()
        self._refresh_past()

    def _app(self) -> NetHackersApp:
        return cast("NetHackersApp", self.app)

    _STATUS_TAG = {"done": "✓ done", "stopped": "■ stopped", "failed": "✗ failed"}

    def _run_label(self, run) -> str:
        st = run.state
        if run.running:
            head = f"⚔ {run.cfg.objective}   {st.get('phase', '')}"
        else:  # finished this session -> a static status head, still reopenable
            head = f"{self._STATUS_TAG.get(run.status, run.status)}   {run.cfg.objective}"
        return (f"{head}   gen {st.get('generation', 0)}   w {st.get('wins', 0)}   "
                f"{_compact(run.total_tokens())} tok   ⏱ {_clock(run.run_time())}")

    def _refresh_session(self) -> None:
        # Every run started THIS session (live AND finished): their full Run is
        # still in memory, so each reopens its complete monitor. Running first,
        # then most-recent. A run never leaves self._runs, so the list only grows.
        runs = sorted(self._app()._runs.values(), key=lambda r: (not r.running, -r.started))
        container = self.query_one("#runs_ongoing", Vertical)
        current = [r.rid for r in runs]
        # Reconcile incrementally -- never remove_children()+remount: removal is
        # async, so re-mounting a still-present id raises DuplicateIds. Track the
        # mounted ids in self._ongoing_ids (updated synchronously) so a second
        # refresh before a pending mount lands doesn't double-mount.
        for rid in self._ongoing_ids:
            if rid not in current:  # (defensive: runs don't currently leave the registry)
                with contextlib.suppress(NoMatches):
                    self.query_one(f"#ongoing-{rid}", Button).remove()
        for run in runs:
            if run.rid in self._ongoing_ids:  # update the label in place (live runs tick)
                # (NoMatches: its mount is still pending -- refreshes next tick)
                with contextlib.suppress(NoMatches):
                    self.query_one(f"#ongoing-{run.rid}", Button).label = self._run_label(run)
            else:  # a new run -> mount one button for it
                container.mount(Button(self._run_label(run),
                                       id=f"ongoing-{run.rid}", classes="ongoing-run"))
        self._ongoing_ids = current
        n_live = sum(1 for r in runs if r.running)
        if not runs:
            title = "[dim]No runs yet. Start one from the ⚔ Evolve tab.[/]"
        else:
            flight = f" · {n_live} in flight" if n_live else ""
            title = f"● {len(runs)} run(s) this session{flight} — enter to open"
        self.query_one("#runs_ongoing_title", Static).update(title)

    def _refresh_past(self) -> None:
        from nethackers.tui.screens.home import recent_runs_panel

        # Older runs from earlier sessions -- read from disk, shown as a summary
        # only (no in-memory Run to reopen). Exclude every run from THIS session
        # (live or finished): those are the reopenable buttons above.
        session = set(self._app()._runs)
        past = [r for r in read_runs(load_stage().runs_dir) if r["run_id"] not in session]
        self.query_one("#runs_past", Static).update(
            recent_runs_panel(past) if past else "[dim]No runs from earlier sessions.[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("ongoing-"):  # a single Enter/click jumps into the monitor
            self._app().open_run(bid[len("ongoing-"):])
