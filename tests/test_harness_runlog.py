# tests/test_harness_runlog.py
import json
from datetime import UTC, datetime

from nethackers.harness.loop import IterationResult
from nethackers.harness.runlog import append_metric, metric_record, run_id, write_run_config

_NOW = datetime(2026, 8, 12, 14, 30, 5, tzinfo=UTC)


def test_run_id_timestamp_and_name_slug():
    assert run_id(_NOW) == "20260812-143005"
    assert run_id(_NOW, "My Run!! v2") == "20260812-143005-my-run-v2"


def test_run_id_collision_appends_counter():
    taken = {"20260812-143005", "20260812-143005-1"}
    assert run_id(_NOW, exists=lambda r: r in taken) == "20260812-143005-2"


def test_write_run_config_and_append_metric(tmp_path):
    d = tmp_path / "runs" / "r1"
    write_run_config(d, {"run_id": "r1", "objective": "random"})
    assert json.loads((d / "run.json").read_text())["objective"] == "random"
    append_metric(d, {"iteration": 0, "outcome": "baseline"})
    append_metric(d, {"iteration": 1, "outcome": "rejected"})
    lines = (d / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["iteration"] for x in lines] == [0, 1]


def test_metric_record_maps_outcome():
    reg = IterationResult(True, "registered", dev_fitness=0.2, heldout_fitness=0.1,
                          tokens=5, digest="sha256:abc", stopped_reason="completed")
    assert metric_record(3, reg) == {
        "iteration": 3, "outcome": "registered", "reason": "registered",
        "dev_fitness": 0.2, "heldout_fitness": 0.1, "tokens": 5,
        "stopped_reason": "completed", "child_digest": "sha256:abc"}
    base = IterationResult(False, "baseline", dev_fitness=0.05, heldout_fitness=0.05)
    assert metric_record(0, base)["outcome"] == "baseline"
    assert metric_record(2, IterationResult(False, "no-dev-gain"))["outcome"] == "rejected"
    assert metric_record(4, IterationResult(False, "error: boom"))["outcome"] == "error"
