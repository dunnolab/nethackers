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
