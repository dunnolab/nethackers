import asyncio
import json
import threading

from textual.widgets import Button

from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.screens.runs import RunsView, read_runs
from nethackers.tui.status import EvolveConfig


def _mk(run_dir, cfg, metrics):
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(cfg))
    (run_dir / "metrics.jsonl").write_text("\n".join(json.dumps(m) for m in metrics))


def test_read_runs_summarizes_wins_and_best(tmp_path):
    _mk(
        tmp_path / "r-1",
        {
            "run_id": "r-1",
            "objective": "wiz-elf-cha-mal",
            "operator": "claude",
            "iterations": 3,
            "created_at": "2026-08-15T00:00:00",
        },
        [
            {
                "iteration": 0,
                "outcome": "baseline",
                "dev_fitness": 0.30,
                "heldout_fitness": 0.28,
            },
            {
                "iteration": 1,
                "outcome": "rejected",
                "dev_fitness": 0.31,
                "heldout_fitness": None,
            },
            {
                "iteration": 2,
                "outcome": "registered",
                "dev_fitness": 0.44,
                "heldout_fitness": 0.41,
            },
            {
                "iteration": 3,
                "outcome": "rejected",
                "dev_fitness": 0.99,
                "heldout_fitness": 0.98,
            },
        ],
    )
    (tmp_path / "latest").symlink_to("r-1")  # symlink must be ignored
    runs = read_runs(tmp_path)
    assert len(runs) == 1
    r = runs[0]
    assert r["run_id"] == "r-1" and r["wins"] == 1
    assert r["best_dev"] == 0.44 and r["best_held"] == 0.41


def test_read_runs_empty_dir(tmp_path):
    assert read_runs(tmp_path) == []
    assert read_runs(tmp_path / "nope") == []


def test_read_runs_skips_malformed_run_json(tmp_path):
    # run.json is a bare array (not a dict)
    (tmp_path / "r-bad").mkdir()
    (tmp_path / "r-bad" / "run.json").write_text("[]")
    runs = read_runs(tmp_path)
    assert runs == []


def test_read_runs_skips_malformed_metrics_line(tmp_path):
    # Valid run.json but a metrics line is a bare number (not a dict)
    _mk(
        tmp_path / "r-1",
        {
            "run_id": "r-1",
            "objective": "test",
            "operator": "claude",
            "iterations": 2,
            "created_at": "2026-08-15T00:00:00",
        },
        [
            {
                "iteration": 0,
                "outcome": "baseline",
                "dev_fitness": 0.30,
                "heldout_fitness": 0.28,
            },
        ],
    )
    # Append a bare number line (bypassing json.dumps which would quote it)
    (tmp_path / "r-1" / "metrics.jsonl").write_text(
        (tmp_path / "r-1" / "metrics.jsonl").read_text() + "\n42"
    )
    runs = read_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r-1"


def test_read_runs_sorts_newest_first(tmp_path):
    # Create two runs with different created_at timestamps
    _mk(
        tmp_path / "r-1",
        {
            "run_id": "r-1",
            "objective": "wiz-elf-cha-mal",
            "operator": "claude",
            "iterations": 1,
            "created_at": "2026-08-15T00:00:00",
        },
        [
            {
                "iteration": 0,
                "outcome": "registered",
                "dev_fitness": 0.40,
                "heldout_fitness": 0.39,
            },
        ],
    )
    _mk(
        tmp_path / "r-2",
        {
            "run_id": "r-2",
            "objective": "barbarian-dwarf-str-mal",
            "operator": "claude",
            "iterations": 1,
            "created_at": "2026-08-15T01:00:00",
        },
        [
            {
                "iteration": 0,
                "outcome": "registered",
                "dev_fitness": 0.50,
                "heldout_fitness": 0.49,
            },
        ],
    )
    runs = read_runs(tmp_path)
    assert len(runs) == 2
    assert runs[0]["run_id"] == "r-2"  # newest first
    assert runs[1]["run_id"] == "r-1"


class _Plan:
    def __init__(self, run):
        self.rid = "r-abc"
        self.cfg = EvolveConfig("val-dwa-law-fem", "claude", 1)
        self.run = run


async def test_runs_view_lists_ongoing_and_opens_on_select():
    fired = threading.Event()

    def long_run(cb):
        cb["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 2, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 2})
        fired.set()
        cb["stop"].wait(timeout=3)
        return []

    app = NetHackersApp(hub="http://127.0.0.1:1", creds=None, start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app.start_run(_Plan(long_run))
        for _ in range(200):
            if fired.is_set():
                break
            await asyncio.sleep(0.01)
        await pilot.press("escape")  # leave the monitor start_run opened -> Runs tab
        await pilot.pause()

        runs_view = app.query_one(RunsView)
        runs_view._refresh()
        await pilot.pause()
        buttons = list(app.query(".ongoing-run").results(Button))
        assert len(buttons) == 1
        assert "val-dwa-law-fem" in str(buttons[0].label)

        # a single Enter/press jumps into the run's monitor (no interact step)
        buttons[0].press()
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor) and app.screen.run is run

        app.stop_run(run.rid)  # let the worker exit cleanly
        for _ in range(200):
            if not run.running:
                break
            await asyncio.sleep(0.01)
