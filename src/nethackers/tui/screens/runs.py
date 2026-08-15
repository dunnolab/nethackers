"""The Runs hub: ongoing runs (live, from the app's Run registry) on top --
select one to jump into its monitor -- over past runs read from disk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from nethackers.tui.status import _compact

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

_RUNS_DIR = Path.home() / ".nethackers" / "evolve" / "runs"


def _summarize(run_dir: Path) -> dict | None:
    try:
        cfg = json.loads((run_dir / "run.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    wins, best_dev, best_held = 0, None, None
    mfile = run_dir / "metrics.jsonl"
    if mfile.exists():
        for line in mfile.read_text().splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            if m.get("outcome") == "registered":
                wins += 1
                if m.get("dev_fitness") is not None:
                    best_dev = m["dev_fitness"]
                # main renamed the loop's held-out fitness -> validation; keep a
                # fallback so pre-rename runs still show a held score.
                held = m.get("validation_fitness", m.get("heldout_fitness"))
                if held is not None:
                    best_held = held
    return {
        "run_id": cfg.get("run_id", run_dir.name),
        "objective": cfg.get("objective", ""),
        "operator": cfg.get("operator", ""),
        "created_at": cfg.get("created_at", ""),
        "iterations": cfg.get("iterations", 0),
        "wins": wins,
        "best_dev": best_dev,
        "best_held": best_held,
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


class RunsView(VerticalScroll):
    """Ongoing runs (live) as a pick-to-open list, then past runs below."""

    DEFAULT_CSS = """
    RunsView { margin: 1 2; padding: 0 1; height: 1fr; }
    RunsView #runs_ongoing_title { color: #d2a24c; text-style: bold; }
    RunsView #runs_ongoing { height: auto; max-height: 12; margin-bottom: 1; }
    RunsView #runs_past_title { color: #7c745f; margin-top: 1; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.add_class("panel")
        self._ongoing_ids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(id="runs_ongoing_title")
        yield OptionList(id="runs_ongoing")
        yield Static("past runs", id="runs_past_title")
        yield Static(id="runs_past")

    def on_mount(self) -> None:
        self.border_title = "▶ Runs"
        self._refresh()
        self.set_interval(1.0, self._tick)

    def on_show(self) -> None:
        self._refresh()

    def _tick(self) -> None:
        if self.display:  # only while the Runs section is the visible pane
            self._refresh_ongoing()

    def _refresh(self) -> None:
        self._refresh_ongoing()
        self._refresh_past()

    def _app(self) -> NetHackersApp:
        return cast("NetHackersApp", self.app)

    def _ongoing_label(self, run) -> str:
        st = run.state
        return (f"⚔ {run.cfg.objective}   {st.get('phase', '')}   "
                f"gen {st.get('generation', 0)}   w {st.get('wins', 0)}   "
                f"{_compact(run.total_tokens())} tok")

    def _refresh_ongoing(self) -> None:
        runs = [r for r in self._app()._runs.values() if r.running]
        options = self.query_one("#runs_ongoing", OptionList)
        ids = [r.rid for r in runs]
        if ids != self._ongoing_ids:  # a run started/finished -> rebuild
            options.clear_options()
            for run in runs:
                options.add_option(Option(self._ongoing_label(run), id=run.rid))
            self._ongoing_ids = ids
        else:  # same set -> just refresh the live labels in place
            for run in runs:
                options.replace_option_prompt(run.rid, self._ongoing_label(run))
        self.query_one("#runs_ongoing_title", Static).update(
            f"● {len(runs)} run(s) in flight — enter to jump in" if runs
            else "[dim]No runs in flight. Start one from the ⚔ Evolve tab.[/]")

    def _refresh_past(self) -> None:
        from nethackers.tui.screens.home import recent_runs_panel

        ongoing = set(self._ongoing_ids)
        past = [r for r in read_runs(_RUNS_DIR) if r["run_id"] not in ongoing]
        self.query_one("#runs_past", Static).update(
            recent_runs_panel(past) if past else "[dim]No finished runs yet.[/]")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "runs_ongoing" and event.option.id:
            self._app().open_run(event.option.id)  # jump into the run's monitor
