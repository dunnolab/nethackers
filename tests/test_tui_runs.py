import asyncio
import json
import threading

from textual.widgets import Button

from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.screens.runs import RunsView, _summarize, read_runs, run_totals
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
            },
            {
                "iteration": 1,
                "outcome": "rejected",
                "dev_fitness": 0.31,
            },
            {
                "iteration": 2,
                "outcome": "registered",
                "dev_fitness": 0.44,
            },
            {
                "iteration": 3,
                "outcome": "rejected",
                "dev_fitness": 0.99,
            },
        ],
    )
    (tmp_path / "latest").symlink_to("r-1")  # symlink must be ignored
    runs = read_runs(tmp_path)
    assert len(runs) == 1
    r = runs[0]
    assert r["run_id"] == "r-1" and r["wins"] == 1
    assert r["best_dev"] == 0.44
    assert "best_held" not in r  # held/validation is gone -- MAP-Elites has no held column


def test_read_runs_counts_local_only_wins_and_their_fitness_too(tmp_path):
    # A "local-only" outcome (runlog.metric_record's new local-only-vs-hub
    # distinction) is still a REAL, accepted local elite -- the Runs list's
    # win count and best-fitness must not silently drop it just because it
    # never reached the hub.
    _mk(
        tmp_path / "r-2",
        {"run_id": "r-2", "objective": "wiz-elf-cha-mal", "operator": "claude",
         "iterations": 1, "created_at": "2026-08-26T00:00:00"},
        [
            {"iteration": 0, "outcome": "baseline", "dev_fitness": 0.30},
            {"iteration": 1, "outcome": "local-only", "dev_fitness": 0.50,
             "hub_reason": "local-only: not published (no gh publisher / dev owner)"},
        ],
    )
    runs = read_runs(tmp_path)
    r = next(x for x in runs if x["run_id"] == "r-2")
    assert r["wins"] == 1
    assert r["best_dev"] == 0.50


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
            },
        ],
    )
    runs = read_runs(tmp_path)
    assert len(runs) == 2
    assert runs[0]["run_id"] == "r-2"  # newest first
    assert runs[1]["run_id"] == "r-1"


# --- _summarize / run_totals: operator-token accounting --------------------


def test_summarize_sums_tokens_across_metrics_lines(tmp_path):
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
            {"iteration": 0, "outcome": "baseline", "tokens": 1000},
            {"iteration": 1, "outcome": "rejected"},  # no tokens -> contributes 0
            {
                "iteration": 2,
                "outcome": "registered",
                "tokens": 4000,
                "dev_fitness": 0.44,
            },
        ],
    )
    summary = _summarize(tmp_path / "r-1")
    assert summary is not None
    assert summary["tokens"] == 5000  # 1000 + 0 (missing) + 4000
    assert summary["wins"] == 1


def test_run_totals_aggregates_and_tolerates_missing_fields():
    runs = [
        {"run_id": "r-1", "wins": 1, "iterations": 3, "tokens": 5000},
        {"run_id": "r-2", "wins": 2, "iterations": 4, "tokens": 1500},
        {"run_id": "r-3"},  # a partial summary -> contributes zeros, no KeyError
    ]
    assert run_totals(runs) == {"runs": 3, "wins": 3, "iterations": 7, "tokens": 6500}


def test_run_totals_empty_is_all_zeros():
    assert run_totals([]) == {"runs": 0, "wins": 0, "iterations": 0, "tokens": 0}


def test_run_totals_over_read_runs_sums_tokens(tmp_path):
    _mk(
        tmp_path / "r-1",
        {
            "run_id": "r-1",
            "objective": "wiz-elf-cha-mal",
            "operator": "claude",
            "iterations": 2,
            "created_at": "2026-08-15T00:00:00",
        },
        [
            {"iteration": 0, "outcome": "baseline", "tokens": 1000},
            {
                "iteration": 1,
                "outcome": "registered",
                "tokens": 3000,
                "dev_fitness": 0.44,
            },
        ],
    )
    _mk(
        tmp_path / "r-2",
        {
            "run_id": "r-2",
            "objective": "val-dwa-law-fem",
            "operator": "claude",
            "iterations": 1,
            "created_at": "2026-08-15T01:00:00",
        },
        [
            {
                "iteration": 0,
                "outcome": "registered",
                "tokens": 500,
                "dev_fitness": 0.50,
            },
        ],
    )
    totals = run_totals(read_runs(tmp_path))
    assert totals["runs"] == 2
    assert totals["tokens"] == 4500  # 1000 + 3000 + 500, summed across both runs
    assert totals["wins"] == 2
    assert totals["iterations"] == 3


def test_reconstruct_run_rebuilds_from_disk_and_flags_no_seed_detail(tmp_path):
    from nethackers.tui.screens.runs import reconstruct_run

    run_dir = tmp_path / "20260916-abc-sam"
    _mk(run_dir, {
        "run_id": "20260916-abc-sam", "objective": "sam-hum-law-fem,val-dwa-law-fem",
        "operator": "claude", "iterations": 3, "model": "sonnet", "effort": "high",
        "operator_version": "claude-code 1.0", "created_at": "2026-09-16T13:00:00",
    }, [
        {"iteration": 0, "outcome": "baseline", "dev_fitness": 0.07},
        {"iteration": 1, "outcome": "rejected", "reason": "no dev gain", "dev_fitness": 0.06,
         "tokens": 1000, "causes": {"killed by a jackal": 2}},
        {"iteration": 2, "outcome": "registered", "reason": "registered", "dev_fitness": 0.12,
         "improved": ["sam-hum-law-fem", "union"], "tokens": 3000, "child_digest": "sha256:dead"},
    ])
    logs = run_dir / "logs"
    logs.mkdir()
    (logs / "iter-2-3.log").write_text(  # run.tag(2) == "iter 2/3" -> _slug -> iter-2-3.log
        '{"type":"assistant","message":{"content":[{"type":"text","text":"try altars"}]}}\n')

    run = reconstruct_run(run_dir)
    assert run is not None
    assert run.reopened is True and run.status == "done"
    assert run.cfg.backend == "claude" and run.cfg.iterations == 3
    assert run.cfg.model == "sonnet" and run.cfg.effort == "high"
    assert run.identities() == ["sam-hum-law-fem", "val-dwa-law-fem"]   # from the objective
    # baseline (iter 0) skipped; iters 1 (rejected) + 2 (registered) rebuilt
    assert run.iteration_status(1) == "rejected"
    assert run.iteration_status(2) == "registered"
    assert run.iter_results[2].dev_fitness == 0.12
    assert run.iter_results[2].improved == ["sam-hum-law-fem", "union"]
    assert run.iter_results[2].results is None                 # per-seed detail not on disk
    assert run.iter_results[1].causes == {"killed by a jackal": 2}
    assert run.ledger_rows == [(1, False, "no dev gain"), (2, True, "registered")]
    assert run.logs.get(run.tag(2))                                     # transcript replayed


def test_reconstruct_run_missing_record_returns_none(tmp_path):
    from nethackers.tui.screens.runs import reconstruct_run
    assert reconstruct_run(tmp_path / "does-not-exist") is None


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


async def test_ongoing_reconciles_when_set_changes_without_duplicate_ids():
    # regression: a rebuild via remove_children()+mount re-mounted a still-present
    # run id before the async removal ran -> DuplicateIds crash mid-navigation.
    def long_run(cb):
        cb["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 1})
        cb["stop"].wait(timeout=3)
        return []

    app = NetHackersApp(hub="http://127.0.0.1:1", creds=None, start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        pa = _Plan(long_run)
        pa.rid = "run-A"
        ra = app.start_run(pa)
        runs_view = app.query_one(RunsView)
        runs_view._refresh()   # mounts A; tracked _ongoing_ids == [run-A]
        await pilot.pause()
        pb = _Plan(long_run)
        pb.rid = "run-B"
        rb = app.start_run(pb)
        runs_view._refresh()   # set changes [A] -> [A, B]; must NOT re-mount A
        await pilot.pause()
        buttons = list(app.query(".ongoing-run").results(Button))
        assert {b.id for b in buttons} == {"ongoing-run-A", "ongoing-run-B"}

        app.stop_run("run-A")
        app.stop_run("run-B")
        for _ in range(200):
            if not (ra.running or rb.running):
                break
            await asyncio.sleep(0.01)


async def test_finished_this_session_run_stays_listed_and_reopens_its_monitor():
    # feature: a run that finished THIS session keeps its full Run in memory, so
    # it must stay in the Runs list as a clickable button (not drop to the
    # read-only "earlier sessions" summary) and reopen its complete monitor.
    def quick_run(cb):
        cb["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 1, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 1})
        return []  # returns immediately -> the run finishes ("done")

    app = NetHackersApp(hub="http://127.0.0.1:1", creds=None, start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app.start_run(_Plan(quick_run))
        for _ in range(200):  # wait for the worker to finish
            if not run.running:
                break
            await asyncio.sleep(0.01)
        await pilot.pause()
        if isinstance(app.screen, RunMonitor):  # start_run opened it -> back to Runs
            await pilot.press("escape")
            await pilot.pause()

        app.query_one(RunsView)._refresh()
        await pilot.pause()
        buttons = list(app.query(".ongoing-run").results(Button))
        assert len(buttons) == 1                       # the finished run is still listed
        assert buttons[0].id == f"ongoing-{run.rid}"
        assert not run.running                         # ...and it really did finish
        assert "done" in str(buttons[0].label)         # shows a finished-status head

        buttons[0].press()                             # reopen its full monitor
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor) and app.screen.run is run
