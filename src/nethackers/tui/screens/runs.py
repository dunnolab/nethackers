from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


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
                if m.get("heldout_fitness") is not None:
                    best_held = m["heldout_fitness"]
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
    def compose(self) -> ComposeResult:
        yield Static(id="runs_body")

    def on_mount(self) -> None:
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        from nethackers.tui.screens.home import recent_runs_panel  # type: ignore[import-not-found]

        runs = read_runs(Path.home() / ".nethackers" / "evolve" / "runs")
        self.query_one("#runs_body", Static).update(recent_runs_panel(runs))
