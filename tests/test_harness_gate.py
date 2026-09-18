# tests/test_harness_gate.py
import json
from pathlib import Path

from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import _solution_digest
from nethackers.harness.gate import passes_gate

SMOKE = ObjectiveSpec(name="smoke", kind="identity", batch=((0, "val-dwa-law-fem"),),
                      max_steps=50, no_progress_timeout=50, action_timeout_seconds=5.0,
                      aggregation="mean")


def _tree(root, body="x = 1\n", entrypoint="bot.py"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "nethackers.solution.json").write_text(json.dumps(
        {"schema": "nethackers.solution/v1", "name": "t", "root": ".",
         "parents": [], "influences": [], "entrypoint": entrypoint}))
    (root / entrypoint).write_text(body)
    return root


def _runner(status):
    def fake(cmd, check, input=None):
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps([{
            "trajectory_id": 0, "status": status, "progress": 0.1, "ascended": False,
            "steps": 1, "turns": 1, "max_depth": 1, "end_status": "died", "error": None,
            "wall_seconds": 0.1, "character": "val-dwa-law-fem", "milestone": None}]))
    return fake


def test_gate_passes_a_changed_running_bot(tmp_path):
    ok, reason = passes_gate(_tree(tmp_path / "c"), "sha256:PARENT",
                             smoke_spec=SMOKE, image="i", now="t", runner=_runner("completed"))
    assert ok, reason


def test_gate_fails_identical_to_parent(tmp_path):
    tree = _tree(tmp_path / "c")
    ok, reason = passes_gate(tree, _solution_digest(tree),
                             smoke_spec=SMOKE, image="i", now="t", runner=_runner("completed"))
    assert not ok and "parent" in reason.lower()


def test_gate_fails_on_crash(tmp_path):
    ok, reason = passes_gate(_tree(tmp_path / "c"), "sha256:PARENT",
                             smoke_spec=SMOKE, image="i", now="t", runner=_runner("bot_error"))
    assert not ok and "smoke" in reason.lower()


def test_gate_fails_missing_entrypoint(tmp_path):
    tree = _tree(tmp_path / "c", entrypoint="bot.py")
    (tree / "bot.py").unlink()
    ok, reason = passes_gate(tree, "sha256:PARENT",
                             smoke_spec=SMOKE, image="i", now="t", runner=_runner("completed"))
    assert not ok and "entrypoint" in reason.lower()
