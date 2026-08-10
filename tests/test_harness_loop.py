# tests/test_harness_loop.py
import json
from pathlib import Path

from nethackers.harness.loop import run_loop
from nethackers.harness.store import LocalTreeStore


def _seed_tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "nethackers.solution.json").write_text(json.dumps(
        {"schema": "nethackers.solution/v1", "name": "seed", "root": ".",
         "parents": [], "influences": [], "entrypoint": "bot.py"}))
    (root / "bot.py").write_text("VERSION = 0\n")
    return root


class _FakeHub:
    def __init__(self): self.registered = []
    def register(self, *, token, reference, manifest, evidence):
        self.registered.append(evidence["solution_digest"])
        return {}


class _ImprovingOperator:
    """Edits bot.py so the child differs; a counter drives rising fitness."""
    def __init__(self): self.n = 0
    def run(self, worktree, brief, *, token_budget, timeout_s):
        from nethackers.harness.operator import OperatorResult
        self.n += 1
        (Path(worktree) / "bot.py").write_text(f"VERSION = {self.n}\n")
        return OperatorResult(backend="fake", tokens=10, stopped_reason="completed")


def _fitness_runner(progress_by_version):
    """Fake Docker runner: reads the mounted bot's VERSION, scores by table."""
    def fake(cmd, check):
        sol = next(v.removesuffix(":/sol:ro") for v in cmd if v.endswith(":/sol:ro"))
        version = int(Path(sol, "bot.py").read_text().split("=")[1])
        batch = json.loads(cmd[cmd.index("--batch") + 1])
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps([
            {"trajectory_id": s, "status": "completed", "progress": progress_by_version(version),
             "ascended": False, "steps": 1, "turns": 1, "max_depth": 1, "end_status": "died",
             "error": None, "wall_seconds": 0.1, "character": c, "milestone": None}
            for s, c in batch]))
    return fake


def test_loop_registers_an_improvement(tmp_path):
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        token_budget=1000, timeout_s=999, heldout_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert results[0].registered is True
    assert len(hub.registered) == 1


def test_loop_discards_a_non_improvement(tmp_path):
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        token_budget=1000, timeout_s=999, heldout_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work")  # flat: no gain
    assert results[0].registered is False
    assert hub.registered == []
