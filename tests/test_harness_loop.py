# tests/test_harness_loop.py
import json
from pathlib import Path

import pytest

from nethackers.harness import loop as loop_mod
from nethackers.harness.loop import IterationResult, run_loop
from nethackers.harness.metering import TokenUsage
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
    def run(self, worktree, brief, *, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.n += 1
        if on_line is not None:
            on_line('{"type":"assistant","message":{"content":'
                    '[{"type":"text","text":"editing"}]}}')
        (Path(worktree) / "bot.py").write_text(f"VERSION = {self.n}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


class _RaisingOperator:
    """Simulates a mutation step that blows up (e.g. the coding agent's CLI
    crashes) -- the loop must discard just this iteration, not abort."""
    def run(self, worktree, brief, *, on_line=None, stop=None):
        raise RuntimeError("boom")


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
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert results[0].registered is True
    assert len(hub.registered) == 1


def test_loop_records_faithful_usage(tmp_path):
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    win = results[-1]
    assert win.registered is True
    assert win.usage == TokenUsage(1, 2, 3, 4) and win.usage.total == 10


def test_loop_discards_a_non_improvement(tmp_path):
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work")  # flat: no gain
    assert results[0].registered is False
    assert hub.registered == []


def test_loop_discards_an_iteration_that_raises(tmp_path):
    """Cold start (fake `runner`) succeeds; the operator raises on the one
    mutation attempt -- that iteration must be discarded, not propagate."""
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_RaisingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert len(results) == 1
    assert results[0].registered is False
    assert results[0].reason.startswith("error:")
    assert hub.registered == []


def test_loop_reports_progress(tmp_path):
    """run_loop streams phase events through the injected `report` callback so
    a caller can show live progress during the (slow) real loop."""
    events: list[str] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z", report=events.append,
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    text = "\n".join(events)
    assert "cold-start" in text      # cold-start scoring announced
    assert "mutating" in text        # per-iteration phases announced
    assert "REGISTERED" in text      # the win is announced live


def test_loop_emits_state_transitions(tmp_path):
    states = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_state=states.append)
    phases = [s["phase"] for s in states]
    assert phases[0] == "cold-start"
    assert "mutating" in phases and "registered" in phases and phases[-1] == "done"
    reg = next(s for s in states if s["phase"] == "registered")
    assert reg["iteration"] == 1 and reg["wins"] == 1
    assert reg["best_dev"] > reg["baseline_dev"]  # improved over the seed


def test_iteration_result_stopped_reason_defaults_to_none():
    assert IterationResult(False, "baseline").stopped_reason is None


def test_on_iteration_fires_for_baseline_and_each_iteration(tmp_path):
    seen: list[tuple[int, str]] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_iteration=lambda i, r: seen.append((i, r.reason)))
    assert seen[0][0] == 0 and seen[0][1] == "baseline"     # cold-start baseline
    assert seen[1][0] == 1                                   # iteration 1 recorded
    assert any(r == "registered" for _i, r in seen)          # its outcome flowed through


def test_loop_forwards_tagged_log_lines(tmp_path):
    logs = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=1,
validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_log=lambda tag, line: logs.append((tag, line)))
    assert logs and logs[0][0] == "iter 1/1" and "editing" in logs[0][1]


def _run_with_migration(tmp_path, monkeypatch, *, migrate, better_version=5, score=0.99):
    """Seed baseline scores VERSION=0 (dev 0.0). Monkeypatch top_trusted_elite to
    offer a VERSION=`better_version` tree (from the store) at `score`. Returns
    the on_state phase dicts seen."""
    store = LocalTreeStore(tmp_path / "store")
    better = _seed_tree(tmp_path / "better")
    (better / "bot.py").write_text(f"VERSION = {better_version}\n")
    better_digest = store.save(better)
    entry = {"solution_digest": better_digest, "score": score, "owner": "other",
             "tier": "verified", "repo": "r", "commit_sha": "c"}
    monkeypatch.setattr(loop_mod, "top_trusted_elite",
                        lambda hub, obj, s, owner, **kw: (entry, store.path(better_digest)))
    states: list[dict] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=store, operator=_ImprovingOperator(), hub=_FakeHub(),
        image="img:dev", token="dev-token", owner="dev", iterations=1,
validation_n=3, migrate=migrate,
        now_fn=lambda: "2026-08-10T00:00:00Z", on_state=states.append,
        runner=_fitness_runner(lambda v: 0.1 * v), workdir=tmp_path / "work")
    return states


def test_loop_migrates_to_strictly_better_hub_elite(tmp_path, monkeypatch):
    states = _run_with_migration(tmp_path, monkeypatch, migrate=True, better_version=5)
    migrated = [s for s in states if s["phase"] == "migrated"]
    assert len(migrated) == 1
    assert migrated[0]["detail"].startswith("other/")
    assert migrated[0]["best_dev"] == pytest.approx(0.5)   # re-eval of VERSION=5


def test_loop_no_migration_when_disabled(tmp_path, monkeypatch):
    states = _run_with_migration(tmp_path, monkeypatch, migrate=False)
    assert not any(s["phase"] == "migrated" for s in states)


def test_loop_no_migration_when_same_digest(tmp_path, monkeypatch):
    # VERSION=0 -> byte-identical tree/digest to the seed; the digest guard
    # blocks even though the offered score (0.99) beats the baseline.
    states = _run_with_migration(tmp_path, monkeypatch, migrate=True, better_version=0)
    assert not any(s["phase"] == "migrated" for s in states)


def test_loop_no_migration_when_not_strictly_better(tmp_path, monkeypatch):
    # a genuinely different tree (VERSION=7 -> different digest), but the offered
    # score (0.0) does not exceed the seed baseline's dev_fitness (0.0).
    states = _run_with_migration(tmp_path, monkeypatch, migrate=True,
                                 better_version=7, score=0.0)
    assert not any(s["phase"] == "migrated" for s in states)
