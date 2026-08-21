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


class _KilledOperator:
    """Operator that reports a manual stop (stopped_reason="killed"). A kill
    returns normally (run_operator does not raise on a kill), so it must NOT
    count toward the operator-error breaker -- it falls through to the normal
    gate path."""
    def run(self, worktree, brief, *, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        return OperatorResult(backend="codex", usage=TokenUsage(), stopped_reason="killed")


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
    # M1: the loop accepts the win as its new local elite but no longer
    # auto-publishes to the hub (register_win is a no-op) -- hub stays untouched.
    assert hub.registered == []


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
    mutation attempt -- that iteration must be discarded, not propagate. A
    raise from operator.run is classified as an operator-error (same breaker
    path as a non-zero exit), not the generic catch-all "error:"."""
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
    assert results[0].reason.startswith("operator-error")
    assert hub.registered == []


def test_loop_circuit_breaker_stops_after_consecutive_raising_operators(tmp_path):
    """A RAISING operator.run (e.g. subprocess.Popen's FileNotFoundError for a
    missing/renamed CLI binary) must trip the SAME breaker as a non-zero exit
    -- otherwise a persistently-broken operator fast-spins the whole
    `iterations` budget with no backoff, which is exactly what the breaker
    exists to prevent."""
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_RaisingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=10,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        max_consecutive_errors=3, sleep=slept.append)
    # 10 iterations requested, but 3 straight raises trip the breaker exactly
    # like 3 straight non-zero exits do -- a raise IS an operator-error.
    assert len(results) == 3
    assert all(r.reason.startswith("operator-error") for r in results)
    assert slept == [1, 2]   # backoff after error 1 and 2; error 3 breaks (no sleep)


def test_loop_non_operator_raise_does_not_trip_the_operator_breaker(tmp_path, monkeypatch):
    """A raise from an UNRELATED step (here: the gate) is not an
    operator-error -- it must keep falling through to the generic outer
    `except` (reason "error:") and must NOT increment the operator-breaker's
    consecutive_errors. max_consecutive_errors=1 would trip on a single
    operator-error; proving 3 straight gate-raises survive it shows the two
    breaker paths are genuinely separate."""
    def _boom(*a, **k):
        raise RuntimeError("gate blew up")
    monkeypatch.setattr(loop_mod, "passes_gate", _boom)
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=3,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        max_consecutive_errors=1, sleep=slept.append)
    assert len(results) == 3
    assert all(r.reason.startswith("error:") for r in results)
    assert slept == []


def test_loop_no_gain_does_not_trip_the_breaker(tmp_path):
    # a HEALTHY operator that never improves must run all iterations --
    # an unlucky run is not an operator error.
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=4,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work",   # flat: no gain
        max_consecutive_errors=3, sleep=lambda _s: None)
    assert len(results) == 4
    assert all(not r.registered for r in results)


def test_loop_killed_operator_does_not_trip_the_breaker(tmp_path):
    # A killed operator (stopped_reason="killed") must NOT be classified as an
    # operator-error -- it returns normally and falls through to the normal gate
    # path (here: rejected as a no-op, since the killed operator never touched
    # the worktree) without ever incrementing the breaker.
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_KilledOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=5,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        max_consecutive_errors=3, sleep=slept.append)
    assert len(results) == 5   # all 5 iterations ran; the breaker never fired
    assert not any(r.reason.startswith("operator-error") for r in results)
    assert slept == []   # no backoff -- the error branch was never entered


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


def test_on_state_carries_parent_snapshot_and_generation(tmp_path):
    """Every on_state payload additionally carries the parent-elite snapshot
    (digest/dev/held) and the generation -- the iteration being worked, which
    advances every attempt -- so a later TUI shows lineage + live progress."""
    hub = _FakeHub()
    seed_tree = _seed_tree(tmp_path / "seed")
    tree_store = LocalTreeStore(tmp_path / "store")
    cold_seed_digest = tree_store.save(seed_tree)  # same digest run_loop computes
    states: list[dict] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=seed_tree,
        tree_store=tree_store, operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="t", owner="o", iterations=1,
        validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_state=states.append)
    phases = {s["phase"]: s for s in states}

    cold = phases["cold-start"]
    assert cold["generation"] == 0
    assert cold["parent_digest"] == cold_seed_digest

    mut = phases["mutating"]
    assert set(mut) >= {"parent_digest", "parent_dev", "parent_held", "generation"}
    assert mut["generation"] == 1                    # iteration 1 (advances per attempt)
    assert mut["parent_digest"] == cold_seed_digest   # mutated from the seed elite
    assert mut["parent_dev"] == cold["best_dev"]
    assert mut["parent_held"] == cold["best_held"]


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
