import json
from pathlib import Path

from nethackers.tui.screens.runs import _summarize, run_causes


def _write_run(dir_: Path, lines: list[dict]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "run.json").write_text(json.dumps({"run_id": dir_.name, "objective": "o"}))
    (dir_ / "metrics.jsonl").write_text("\n".join(json.dumps(m) for m in lines) + "\n")


def test_summarize_sums_causes_across_iterations(tmp_path: Path):
    _write_run(tmp_path / "r1", [
        {"outcome": "baseline", "causes": {"killed by a jackal": 2, "starved to death": 1}},
        {"outcome": "rejected", "causes": {"killed by a jackal": 1}},
        {"outcome": "rejected", "causes": None},
    ])
    assert _summarize(tmp_path / "r1")["causes"] == {
        "killed by a jackal": 3, "starved to death": 1}


def test_summarize_defaults_causes_for_legacy_lines(tmp_path: Path):
    _write_run(tmp_path / "r2", [{"outcome": "baseline", "dev_fitness": 0.1}])
    assert _summarize(tmp_path / "r2")["causes"] == {}


def test_run_causes_sums_across_runs():
    runs = [
        {"causes": {"killed by a jackal": 2}},
        {"causes": {"killed by a jackal": 1, "drowned in a moat": 4}},
        {},  # a run summarized before this field existed
    ]
    assert run_causes(runs) == {"killed by a jackal": 3, "drowned in a moat": 4}
