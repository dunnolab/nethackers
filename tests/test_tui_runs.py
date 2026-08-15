import json

from nethackers.tui.screens.runs import read_runs


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
