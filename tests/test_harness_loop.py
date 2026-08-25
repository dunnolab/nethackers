# tests/test_harness_loop.py
import json
import random
from pathlib import Path

import pytest

from nethackers.contracts.models import Evidence, Objective
from nethackers.harness import loop as loop_mod
from nethackers.harness.loop import EliteState, IterationResult, run_loop, select_reseed
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
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
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
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        raise RuntimeError("boom")


class _KilledOperator:
    """Operator that reports a manual stop (stopped_reason="killed"). A kill
    returns normally (run_operator does not raise on a kill), so it must NOT
    count toward the operator-error breaker -- it falls through to the normal
    gate path."""
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        return OperatorResult(backend="codex", usage=TokenUsage(), stopped_reason="killed")


class _RefCapturingOperator:
    def __init__(self): self.seen = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.seen.append(refs)
        # call 1: regresses (rejected) -> call 2: improves enough for a
        # validated dev+validation win -> call 3: anything -- only used to
        # observe the refs dir handed to the NEXT call after the win.
        v = {1: 1, 2: 5}.get(len(self.seen), 9)
        (Path(worktree)/"bot.py").write_text(f"VERSION = {v}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


class _AlwaysRejectingOperator:
    """Every call mutates to a distinct, never-seen-before VERSION but never
    beats the seed's baseline fitness -- every iteration is rejected as
    no-dev-gain, so `elite.recent_attempts` keeps accumulating across the
    whole run (proving the <=3 bound actually trims, not just never fills)."""
    def __init__(self): self.seen = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.seen.append(refs)
        (Path(worktree)/"bot.py").write_text(f"VERSION = {len(self.seen)}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


class _ParentVersionRecordingOperator:
    """Records the parent VERSION it is handed -- read from `bot.py` at the
    START of the call, before editing anything -- into `.seen`, then writes
    an incremented VERSION. Used to externally observe exactly which
    lineage (parent) each call was based on, which is what distinguishes
    isolated islands (each sees only ITS OWN prior wins) from a single
    shared elite (every call would see the globally-latest win)."""
    def __init__(self): self.seen: list[int] = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        path = Path(worktree) / "bot.py"
        version = int(path.read_text().split("=")[1])
        self.seen.append(version)
        path.write_text(f"VERSION = {version + 1}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


class _ParentVersionRecordingCounterOperator:
    """Like `_ParentVersionRecordingOperator` (records the parent VERSION it
    reads before mutating, into `.seen`), but writes a GLOBALLY incrementing
    call counter as the new VERSION instead of `version + 1`. That makes
    each of N calls produce a distinct, call-indexed VERSION (1, 2, 3, ...)
    regardless of which island/lineage it was handed -- so a fitness table
    keyed on the call index can score each call independently of parentage,
    which is what the B2 reset test below needs to prove reseeding actually
    happened (rather than merely being consistent with either outcome)."""
    def __init__(self):
        self.seen: list[int] = []
        self.n = 0

    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        path = Path(worktree) / "bot.py"
        version = int(path.read_text().split("=")[1])
        self.seen.append(version)
        self.n += 1
        path.write_text(f"VERSION = {self.n}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


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


def _fitness_runner_by_character(progress_fn):
    """Fake Docker runner like `_fitness_runner`, but keyed on (version,
    character) instead of version alone -- lets a test give per-identity
    progress that diverges by build (one build up, another down) rather than
    every identity moving in lockstep."""
    def fake(cmd, check):
        sol = next(v.removesuffix(":/sol:ro") for v in cmd if v.endswith(":/sol:ro"))
        version = int(Path(sol, "bot.py").read_text().split("=")[1])
        batch = json.loads(cmd[cmd.index("--batch") + 1])
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps([
            {"trajectory_id": s, "status": "completed", "progress": progress_fn(version, c),
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
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40})
    assert results[0].registered is True
    assert len(hub.registered) == 1


def test_loop_win_without_publisher_is_a_local_elite(tmp_path):
    # No `publish` hook -> the win is accepted as a local elite only, never
    # registered against the hub (no synthetic, unfetchable reference).
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert results[0].registered is True   # still a new (local) elite
    assert hub.registered == []            # but nothing went to the hub


class _FailingHub:
    """register always fails (hub down / a 400). A validation-confirmed win must
    still be kept as a local elite -- never discarded over a hub-side failure."""
    def register(self, *, token, reference, manifest, evidence):
        raise RuntimeError("hub 400")


def test_loop_keeps_win_local_when_register_fails(tmp_path):
    # iter1 (v1=0.40) beats the seed (v0=0.20) -> a validated win, but the hub
    # register raises. The win must be KEPT as the local elite (registered=True,
    # not recorded as an error), and the elite must ADVANCE to 0.40 -- proven by
    # iter2 (v2=0.30) being rejected as no-dev-gain against it, not counted a win.
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FailingHub(), image="img:dev", token="dev-token", owner="dev", iterations=2,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: [0.20, 0.40, 0.30][v]), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40})
    assert results[0].registered is True         # win kept despite the register 400
    assert results[0].reason == "registered"     # not "error:hub 400"
    assert results[1].registered is False         # elite advanced to 0.40...
    assert results[1].reason == "no-dev-gain"     # ...so iter2's 0.30 is rejected


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


def test_loop_provisions_refs_with_recent_rejects(tmp_path):
    op = _RefCapturingOperator()
    run_loop(objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path/"seed"),
             tree_store=LocalTreeStore(tmp_path/"store"), operator=op, hub=_FakeHub(),
             image="img:dev", token="t", owner="dev", iterations=3, validation_n=3,
             now_fn=lambda: "2026-08-25T00:00:00Z",
             runner=_fitness_runner(lambda v: {0: 0.2, 1: 0.1, 5: 0.9, 9: 0.5}[v]),
             workdir=tmp_path/"work")
    # iter 1 (v1=0.1) is rejected → iter 2's refs dir contains it under attempts/
    refs2 = op.seen[1]
    assert refs2 is not None
    assert any(p.name.startswith("iter") for p in (refs2/"attempts").iterdir())
    assert (refs2/"CONTEXT.md").exists()
    # iter 2 (v5=0.9) beats the seed's 0.2 on both dev and validation -> a
    # REAL registered win, which clears recent_attempts -- iter 3's refs dir
    # (assembled from the post-win elite) must carry no attempts/ at all.
    refs3 = op.seen[2]
    assert refs3 is not None
    assert not (refs3/"attempts").exists()


def test_loop_bounds_recent_attempts_to_three_dropping_oldest(tmp_path):
    op = _AlwaysRejectingOperator()
    run_loop(objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path/"seed"),
             tree_store=LocalTreeStore(tmp_path/"store"), operator=op, hub=_FakeHub(),
             image="img:dev", token="t", owner="dev", iterations=5, validation_n=3,
             now_fn=lambda: "2026-08-25T00:00:00Z",
             runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),  # every mutant regresses
             workdir=tmp_path/"work")
    # 4 straight no-dev-gain rejects (iter-1..iter-4) precede the 5th call ->
    # the list is trimmed to the last 3, dropping the oldest (iter-1).
    refs5 = op.seen[4]
    assert refs5 is not None
    labels = {p.name for p in (refs5/"attempts").iterdir()}
    assert labels == {"iter-2", "iter-3", "iter-4"}


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
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40})
    text = "\n".join(events)
    assert "cold-start" in text      # cold-start scoring announced
    assert "mutating" in text        # per-iteration phases announced
    assert "REGISTERED" in text      # the hub-registered win is announced live


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
    # the brief heads the iteration log (so a reader sees the instruction),
    # then the operator's own stream lines follow.
    assert logs and logs[0][0] == "iter 1/1" and "nethackers_brief" in logs[0][1]
    assert any(tag == "iter 1/1" and "editing" in line for tag, line in logs)


# -- generalist (set) objectives: register a per-identity slice, not a union
# registration; _emit carries the live per-identity parent snapshot (Task 8).

class _SeedSetHub:
    """Records each registration's evidence['objective']['seed_set'] (not
    its solution_digest, unlike _FakeHub) -- so a set win's per-identity
    slices are individually assertable. Has no `.elites`, so the influence
    pool degrades to empty (the per-member lookup is swallowed); these tests
    exercise set REGISTRATION, not island seeding."""
    def __init__(self): self.seed_sets = []
    def register(self, *, token, reference, manifest, evidence):
        self.seed_sets.append(evidence["objective"]["seed_set"])
        return {}


def test_loop_registers_a_slice_per_identity_for_a_set_objective(tmp_path):
    """A set objective ("wiz-elf-cha-mal,wiz-orc-cha-mal") must register ONE
    win per identity (register_win_slices), not a single union registration
    -- and the "mutating" on_state payload must carry the live per-identity
    parent snapshot (identities/parent_means) for a set-aware brief/monitor."""
    hub = _SeedSetHub()
    states: list[dict] = []
    run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40},
        on_state=states.append)

    assert len(hub.seed_sets) == 2
    assert set(hub.seed_sets) == {"wiz-elf-cha-mal", "wiz-orc-cha-mal"}

    mutating = next(s for s in states if s["phase"] == "mutating")
    assert len(mutating["identities"]) == 2
    assert isinstance(mutating["parent_means"], dict)


def test_loop_registers_regressions_on_a_per_identity_drop(tmp_path):
    """The union mean can rise (winning the dev/validation gates) while one
    member identity of a set objective drops relative to the OLD parent --
    aggregate.regressions is dead code today (spec decision C sec 5/9 wants
    it surfaced); the registered IterationResult must carry that regression,
    naming the dropped build."""
    hub = _SeedSetHub()

    def progress(version, character):
        if version == 0:  # cold-start seed: both builds tie at 0.5
            return 0.5
        # the winning child: wiz-elf rises, wiz-orc drops below the parent's 0.5
        return 0.9 if character == "wiz-elf-cha-mal" else 0.3

    results = run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        validation_n=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner_by_character(progress), workdir=tmp_path / "work")

    win = results[-1]
    assert win.registered is True
    assert win.regressions  # non-empty: at least one build dropped
    assert win.regressions[0][0] == "wiz-orc-cha-mal"  # names the dropped build
    assert win.regressions[0][1] < 0                   # a negative delta


def test_loop_no_regressions_for_a_single_identity_objective(tmp_path):
    # identities=[] for a single-identity objective -> regs=[] -> regressions
    # stays None (never an empty list) on the registered result.
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        validation_n=3, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    win = results[-1]
    assert win.registered is True
    assert win.regressions is None


# -- islands (Task B1): the loop holds K island champions, mutated round-robin
# by iteration `k % K`, instead of a single shared `elite`. K=1 (the default)
# must reproduce today's single-lineage behavior exactly.

def test_one_island_is_a_single_advancing_lineage(tmp_path):
    """K=1 (the default -- `islands` omitted) must reproduce today's
    pre-islands behavior exactly: every iteration mutates the SAME lineage,
    so the parent VERSION the operator sees strictly advances one-by-one.
    Contrast with test_two_islands_advance_independently's [0, 0, 1, 1]
    under islands=2 -- the distinguishing signal that islands are isolated."""
    op = _ParentVersionRecordingOperator()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op,
        hub=_FakeHub(), image="img:dev", token="t", owner="o",
        iterations=4, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert op.seen == [0, 1, 2, 3]


def test_two_islands_advance_independently(tmp_path):
    """With islands=2 and a strictly-rising fitness table (every mutation
    wins), iterations 0/2 work island 0 and iterations 1/3 work island 1.
    Each island only ever sees ITS OWN prior win as a parent -- island 0's
    win at iter 0 must NOT become island 1's parent at iter 1. That makes
    the recorded parent-VERSION sequence [0, 0, 1, 1]: island0 sees v0 then
    its own v1; island1 sees v0 (untouched by island0's win) then its own
    v1. A single shared elite would instead produce a monotonic [0, 1, 2, 3]
    (see test_one_island_is_a_single_advancing_lineage) -- this is the
    assertion that actually distinguishes isolated islands from a shared
    elite, so it is intentionally exact rather than a non-empty/inequality
    check."""
    op = _ParentVersionRecordingOperator()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op,
        hub=_FakeHub(), image="img:dev", token="t", owner="o",
        iterations=4, islands=2, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert op.seen == [0, 0, 1, 1]


# -- periodic reset (Task B2): every `reset_period` iterations, the bottom
# half of islands (ranked by champion dev_fitness) is killed and reseeded
# from a top-k-sampled SURVIVING island -- never blindly "the single best"
# -- leaving every survivor (champion + recent_attempts) untouched.

def test_reset_reseeds_weak_island_from_survivor(tmp_path):
    """islands=2, reset_period=2, iterations=4, seed dev/validation=0.5. A
    call-indexed fitness table lets each of the operator's 4 calls score
    independently of which lineage it came from, so the trace is:

      iter0 island0: parent v0 (seed) -> writes v1 -> dev=0.9 WINS
                     (champ becomes v1, dev=validation=0.9)
      iter1 island1: parent v0 (seed) -> writes v2 -> dev=0.1 REJECTED
                     (champ stays the seed, v0/0.5)
      -- reset_period=2 reached: rank by champion dev_fitness -> island1
         (0.5) is the bottom half, island0 (0.9) survives untouched.
         island1 is reseeded from island0's champion (v1).
      iter2 island0: parent v1 (its OWN champ, untouched by the reset)
                     -> writes v3 -> dev=0.95 WINS
      iter3 island1: parent v1 (the RESEED from island0 -- not the seed v0
                     it would still be sitting on without a reset) ->
                     writes v4 -> dev=0.2 REJECTED

    Without the reset this would read [0, 0, 1, 0] (island1 still stuck on
    the seed at iter3) -- so `seen[3] == 1` is what actually distinguishes
    "island1 was reseeded from the survivor" from "islands stayed
    isolated forever" (B1's behavior). `seen[2] == 1` is the same trace's
    proof that island0 (the survivor) was left untouched by that reset.
    """
    op = _ParentVersionRecordingCounterOperator()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op,
        hub=_FakeHub(), image="img:dev", token="t", owner="o",
        iterations=4, islands=2, reset_period=2, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.5, 1: 0.9, 2: 0.1, 3: 0.95, 4: 0.2}[v]),
        workdir=tmp_path / "work")
    assert op.seen == [0, 0, 1, 1]


def test_reset_is_a_noop_with_a_single_island(tmp_path):
    """islands=1 (the default) must never reset, even when `reset_period` is
    small enough to be reached repeatedly within the run -- with only one
    island there's no bottom half to kill and no survivor to reseed from,
    so the single lineage just keeps advancing exactly like pre-B2
    behavior (contrast `test_one_island_is_a_single_advancing_lineage`,
    which proves the same thing without an explicit `reset_period`)."""
    op = _ParentVersionRecordingOperator()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op,
        hub=_FakeHub(), image="img:dev", token="t", owner="o",
        iterations=4, reset_period=2, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert op.seen == [0, 1, 2, 3]


# -- C3: seed islands from the coverage-aware influence pool, and provision
# up to 2 per-iteration `/refs/influences/` (excluding the active island's
# own champion). "wiz-elf-cha-mal" / "wiz-orc-cha-mal" are real IDENTITIES,
# same set used by the coverage-gated tests above and by
# tests/test_harness_select.py.

class _HubByIdentity:
    """Per-identity elite pools (mirrors tests/test_harness_select.py) --
    `.elites(x)` depends on x, unlike `_FakeHub`/`_SeedSetHub` above."""
    def __init__(self, pools): self.pools = pools
    def elites(self, objective): return list(self.pools.get(objective, []))


def _atom_entry(repo_suffix: str, commit_char: str, score: float) -> dict:
    # An ATOM identity (solution_digest == "<repo>@<commit>", no colon) so
    # `select._resolve`'s trust-the-pulled-commit path applies (same as
    # tests/test_harness_select.py's `_atom_entry`) -- a real hub entry, not
    # a self-registered content digest. `repo_suffix` sits right after
    # "github.com/" (not buried deeper in the path) so two entries' digests
    # already differ within the first 12 chars -- what `label=f"hub-
    # {digest[:12]}"` actually keys its folder name on.
    repo = f"github.com/{repo_suffix}/repo"
    commit = commit_char * 40
    return {"solution_digest": f"{repo}@{commit}", "score": score, "owner": "dev",
            "tier": "verified", "repo": repo, "commit_sha": commit}


class _RecordingOperator:
    """Records the parent bot.py content it is handed (read BEFORE mutating,
    so it reflects which lineage/tree the island actually seeded from) and
    the `refs` dir passed in, across every call. Always mutates to a fixed,
    always-losing VERSION so no iteration ever wins -- this test is about
    seeding + refs provisioning, not the win path."""
    def __init__(self):
        self.seen_bot_py: list[str] = []
        self.seen_refs: list[Path] = []

    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        path = Path(worktree) / "bot.py"
        self.seen_bot_py.append(path.read_text())
        self.seen_refs.append(refs)
        path.write_text("VERSION = 999\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


def test_islands_seed_from_pool_and_refs_carry_influences(tmp_path):
    """A SET objective with a populated influence pool must cold-start its K
    islands from the pool's per-identity specialists (not all-K-copies of the
    seed elite), and every iteration's `/refs/influences/` must carry up to 2
    OTHER pool entries -- never the active island's own champion digest."""
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a, entry_b = _atom_entry("a", "a", 0.9), _atom_entry("b", "b", 0.7)
    hub = _HubByIdentity({id_a: [entry_a], id_b: [entry_b]})
    versions = {entry_a["solution_digest"]: 100, entry_b["solution_digest"]: 200}

    def fetch(entry, dest):
        dest = Path(dest)
        (dest / "bot.py").write_text(f"VERSION = {versions[entry['solution_digest']]}\n")
        (dest / "nethackers.solution.json").write_text(json.dumps(
            {"schema": "nethackers.solution/v1", "name": "specialist", "root": ".",
             "parents": [], "influences": [], "entrypoint": "bot.py"}))
        return dest

    op = _RecordingOperator()
    run_loop(
        objective=f"{id_a},{id_b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=2, iterations=2,
        validation_n=3, fetch=fetch,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 100: 0.9, 200: 0.7}.get(v, 0.01)),
        workdir=tmp_path / "work")

    # -- island seeding: with the default k=1 (deterministic argmax), the
    # higher-scored entry_a (0.9) is drawn before entry_b (0.7) -- island0's
    # (iter0) and island1's (iter1) FIRST-seen parent content is a pulled
    # specialist's, never the seed's "VERSION = 0".
    assert op.seen_bot_py[0] == "VERSION = 100\n"
    assert op.seen_bot_py[1] == "VERSION = 200\n"

    # -- /refs/influences/: populated, <=2, and never the active island's own
    # champion -- island0 (seeded from entry_a) is influenced by entry_b, and
    # vice versa for island1.
    refs0, refs1 = op.seen_refs[0], op.seen_refs[1]
    assert (refs0 / "influences").is_dir() and (refs1 / "influences").is_dir()
    labels0 = {p.name for p in (refs0 / "influences").iterdir()}
    labels1 = {p.name for p in (refs1 / "influences").iterdir()}
    assert 1 <= len(labels0) <= 2 and 1 <= len(labels1) <= 2
    # Each label is "hub-<i>-<sanitized digest[:12]>": the positional index
    # keeps two same-owner influences from colliding on one folder (atom
    # digests share a long common prefix), and the "/" in digest[:12] is
    # sanitized (same policy as LocalTreeStore._key) so it names one flat
    # folder. Here each refs dir carries exactly one influence, so i is 0.
    a_label = f"hub-0-{entry_a['solution_digest'][:12].replace('/', '_')}"
    b_label = f"hub-0-{entry_b['solution_digest'][:12].replace('/', '_')}"
    assert labels0 == {b_label}
    assert labels1 == {a_label}

    # CONTEXT.md carries the folder label + the human note (score + which
    # identity the influence is strong at) so the mutator knows what each
    # /refs/influences/ folder is.
    context0 = (refs0 / "CONTEXT.md").read_text()
    assert b_label in context0 and "strong at wiz-orc-cha-mal" in context0


def _specialist_fetch(versions):
    # A `fetch` that materializes a pulled elite's tree from its digest ->
    # VERSION mapping (a valid solution tree so the store/gate accept it).
    def fetch(entry, dest):
        dest = Path(dest)
        (dest / "bot.py").write_text(f"VERSION = {versions[entry['solution_digest']]}\n")
        (dest / "nethackers.solution.json").write_text(json.dumps(
            {"schema": "nethackers.solution/v1", "name": "specialist", "root": ".",
             "parents": [], "influences": [], "entrypoint": "bot.py"}))
        return dest
    return fetch


def test_same_owner_influences_get_distinct_labels(tmp_path):
    """REGRESSION (C1): two elites from the SAME owner have atom digests that
    share their first 12 chars ("github.com/<owner-initial>…"), so the old
    label `hub-<digest[:12]>` collapsed both onto ONE `/refs/influences/`
    folder -- refs.assemble's copytree then raised FileExistsError, which the
    loop's outer except swallowed as an `error:` result, silently burning the
    whole iteration budget on set objectives once the active champion is a
    won (non-pool) digest. The positional index must keep the folders
    distinct so the iteration reaches the operator and completes normally."""
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    repo = "github.com/vkurenkov/nethacker"   # SAME owner+repo, different commit

    def mk(sha, score):
        return {"solution_digest": f"{repo}@{sha}", "score": score, "owner": "dev",
                "tier": "verified", "repo": repo, "commit_sha": sha}
    entry_a, entry_b = mk("1" * 40, 0.9), mk("2" * 40, 0.7)
    d1, d2 = entry_a["solution_digest"], entry_b["solution_digest"]
    assert d1[:12] == d2[:12]   # the exact collision the old label hit
    hub = _HubByIdentity({id_a: [entry_a], id_b: [entry_b]})

    op = _RefCapturingOperator()
    results = run_loop(
        objective=f"{id_a},{id_b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=1, iterations=2,
        validation_n=3, fetch=_specialist_fetch({d1: 100, d2: 200}),
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 1: 0.9, 5: 0.5}.get(v, 0.01)),
        workdir=tmp_path / "work")

    # iter 1 (0-indexed results[1]) must have reached the operator -- a
    # FileExistsError would have aborted refs.assemble BEFORE operator.run,
    # leaving only one recorded call and an `error:` result.
    assert len(op.seen) == 2
    assert not results[1].reason.startswith("error:")
    labels = {p.name for p in (op.seen[1] / "influences").iterdir()}
    assert len(labels) == 2   # both same-owner influences, distinct folders


def test_k1_set_objective_keeps_seed_tree_but_still_gets_influences(tmp_path):
    """I1: at the default K=1 a set objective must NOT swap its single island
    for the pool's union-argmax specialist -- island 0 stays `seed_tree` (in
    production launch's coverage-gated parent), so the first parent the
    operator sees is the seed's "VERSION = 0", never a pulled specialist. The
    pool is still mined for /refs/influences, so K=1 gets the principled
    parent AND cross-elite reference material."""
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a, entry_b = _atom_entry("a", "a", 0.9), _atom_entry("b", "b", 0.7)
    hub = _HubByIdentity({id_a: [entry_a], id_b: [entry_b]})
    versions = {entry_a["solution_digest"]: 100, entry_b["solution_digest"]: 200}

    op = _RecordingOperator()
    run_loop(
        objective=f"{id_a},{id_b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=1, iterations=1,
        validation_n=3, fetch=_specialist_fetch(versions),
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 100: 0.9, 200: 0.7}.get(v, 0.01)),
        workdir=tmp_path / "work")

    assert op.seen_bot_py[0] == "VERSION = 0\n"   # island 0 = seed, not entry_a (v100)
    # active champion is the seed digest (not in the pool), so NEITHER pool
    # entry is filtered as "self" -> both appear as distinct influences.
    labels0 = {p.name for p in (op.seen_refs[0] / "influences").iterdir()}
    assert len(labels0) == 2


def test_from_seed_ignores_hub_for_seeding_and_influences(tmp_path):
    """I2: --from-seed is a deliberate, hub-independent cold start. Even for a
    set objective with a populated pool, every island stays on `seed_tree` and
    NO /refs/influences are provisioned -- the pool is the hub, and --from-seed
    ignores the hub. A `fetch` that raises proves nothing is ever pulled."""
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a, entry_b = _atom_entry("a", "a", 0.9), _atom_entry("b", "b", 0.7)
    hub = _HubByIdentity({id_a: [entry_a], id_b: [entry_b]})

    def _boom_fetch(entry, dest):
        raise AssertionError("--from-seed must not fetch any hub solution")

    op = _RecordingOperator()
    run_loop(
        objective=f"{id_a},{id_b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=2, iterations=2,
        validation_n=3, from_seed=True, fetch=_boom_fetch,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2}.get(v, 0.01)),
        workdir=tmp_path / "work")

    assert op.seen_bot_py == ["VERSION = 0\n", "VERSION = 0\n"]   # both islands = seed
    for refs_dir in op.seen_refs:
        assert not (refs_dir / "influences").exists()   # no hub influences


def test_reset_injects_a_fresh_hub_pool_elite_into_a_killed_island(tmp_path):
    """Cross-run injection: at reset the killed island is reseeded from a FRESH
    hub-pool elite (one no island already holds), not only a local survivor --
    the gentle stand-in for the removed mid-run migration. A,B are the pool's
    top two, so cold-start seeds island0<-A (v100) and island1<-B (v200); C is
    a lower-scored third specialist no island holds. Both islands lose iters
    0/1 (op writes the always-losing v999), so at the reset after iter1
    island0 (dev 0.3) ranks below island1 (0.6) and is killed -- then injected
    with C (v300), A/B being excluded as already-held. iter2 works island0, so
    the parent the operator then sees is C. Without injection it would instead
    be the survivor island1's B (v200)."""
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a, entry_b, entry_c = (_atom_entry("a", "a", 0.9),
                                 _atom_entry("b", "b", 0.8),
                                 _atom_entry("c", "c", 0.5))
    hub = _HubByIdentity({id_a: [entry_a, entry_c], id_b: [entry_b]})
    versions = {entry_a["solution_digest"]: 100, entry_b["solution_digest"]: 200,
                entry_c["solution_digest"]: 300}
    op = _RecordingOperator()
    run_loop(
        objective=f"{id_a},{id_b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=2, reset_period=2,
        iterations=3, validation_n=3, fetch=_specialist_fetch(versions),
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {100: 0.3, 200: 0.6, 300: 0.9}.get(v, 0.01)),
        workdir=tmp_path / "work")

    assert op.seen_bot_py[0] == "VERSION = 100\n"   # island0 <- A (cold-start pool seed)
    assert op.seen_bot_py[1] == "VERSION = 200\n"   # island1 <- B
    assert op.seen_bot_py[2] == "VERSION = 300\n"   # island0 reseeded from C (hub pool)


def test_single_identity_is_treated_as_a_size_one_set_for_the_pool(tmp_path):
    """A single identity is just a set of size one: with a populated hub pool
    and islands>1 it must seed its islands from that identity's own elite board
    (and carry /refs influences), exactly like a multi-identity set -- not fall
    back to all-copies-of-the-seed. This is the asymmetry the old `kind ==
    "set"` gate caused (single -> identities=[] -> pool machinery skipped)."""
    ident = "wiz-elf-cha-mal"
    entry_a, entry_b = _atom_entry("a", "a", 0.9), _atom_entry("b", "b", 0.7)
    hub = _HubByIdentity({ident: [entry_a, entry_b]})   # the identity's own board
    versions = {entry_a["solution_digest"]: 100, entry_b["solution_digest"]: 200}
    op = _RecordingOperator()
    run_loop(
        objective=ident, seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=hub,
        image="img:dev", token="t", owner="dev", islands=2, iterations=2,
        validation_n=3, fetch=_specialist_fetch(versions),
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 100: 0.9, 200: 0.7}.get(v, 0.01)),
        workdir=tmp_path / "work")

    assert op.seen_bot_py[0] == "VERSION = 100\n"   # island0 <- top elite, not the seed
    assert op.seen_bot_py[1] == "VERSION = 200\n"   # island1 <- 2nd elite
    assert (op.seen_refs[0] / "influences").is_dir()   # influences provisioned too


def test_single_identity_win_uses_register_win_not_slices(tmp_path, monkeypatch):
    """The register split stays kind-based even though the POOL now treats a
    single identity as a size-1 set: a single records ONE ordinary win
    (register_win), a true set records a per-identity slice per member
    (register_win_slices). Registration semantics must not change."""
    calls: list[str] = []
    monkeypatch.setattr(loop_mod, "register_win", lambda *a, **k: calls.append("win"))
    monkeypatch.setattr(loop_mod, "register_win_slices", lambda *a, **k: calls.append("slices"))
    run_loop(
        objective="wiz-elf-cha-mal", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o",
        iterations=1, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/o/nethacker", "commit": "a" * 40})
    assert calls == ["win"]   # register_win, never slices, for a single identity


def test_islands_below_one_raises(tmp_path):
    with pytest.raises(ValueError, match="islands must be >= 1"):
        run_loop(
            objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
            tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
            hub=_FakeHub(), image="img:dev", token="t", owner="o",
            iterations=1, islands=0, validation_n=3,
            now_fn=lambda: "2026-08-25T00:00:00Z",
            runner=_fitness_runner(lambda v: 0.2), workdir=tmp_path / "work")


def test_reset_period_below_one_raises(tmp_path):
    with pytest.raises(ValueError, match="reset_period must be >= 1"):
        run_loop(
            objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
            tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
            hub=_FakeHub(), image="img:dev", token="t", owner="o",
            iterations=1, islands=1, reset_period=0, validation_n=3,
            now_fn=lambda: "2026-08-25T00:00:00Z",
            runner=_fitness_runner(lambda v: 0.2), workdir=tmp_path / "work")


def test_set_objective_with_empty_pool_still_cold_starts_from_seed(tmp_path):
    """A set objective whose hub IS reachable (unlike `_SeedSetHub`, which has
    no `.elites` at all) but returns no trusted entries for any member
    identity must still cold-start every island from the seed elite, exactly
    like today -- an empty pool must behave the same as no pool, and
    `/refs/influences/` must stay absent (`refs.assemble` degrades to no
    `influences/` folder when the list is empty)."""
    class _EmptyPoolHub:
        def elites(self, ident): return []
    op = _RecordingOperator()
    run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=_EmptyPoolHub(),
        image="img:dev", token="t", owner="dev", islands=2, iterations=1, validation_n=3,
        now_fn=lambda: "2026-08-25T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert op.seen_bot_py[0] == "VERSION = 0\n"        # unchanged cold start: the seed
    assert not (op.seen_refs[0] / "influences").exists()


def _elite(fitness: float) -> EliteState:
    """A minimal EliteState for unit-testing select_reseed directly. Only
    `dev_fitness` (what select_reseed weighs on) needs to vary between
    calls; `dev_evidence` is a placeholder select_reseed never reads."""
    evidence = Evidence(
        solution_digest=f"sha256:{fitness}", objective=Objective(character=None),
        evaluator_image="img:dev", tier="self-reported", results=(),
        episodes=0, mean_progress=0.0, ascensions=0,
        created_at="2026-08-25T00:00:00Z")
    return EliteState(f"digest-{fitness}", Path(f"/tree-{fitness}"), fitness, fitness, evidence)


def test_select_reseed_samples_by_weighted_fitness_not_argmax():
    """select_reseed must be a temperature-weighted SAMPLE over survivors,
    never a deterministic argmax. The reset tests above can't catch a
    regression to `max(survivors, key=lambda s: s.dev_fitness)` -- both
    ever leave exactly ONE survivor after the kill, where select_reseed's
    own `len(survivors) == 1` fast path returns that survivor outright,
    making a correct weighted sampler and a broken argmax indistinguishable.
    This test passes >=2 survivors directly, which is the only way to
    actually exercise `rng.choices` over `exp(fitness / temperature)`."""
    low, mid, top = _elite(0.1), _elite(0.5), _elite(0.9)
    survivors = [low, mid, top]

    # (a) at least one seed in a small sweep picks a NON-top survivor -- an
    # argmax implementation would return `top` for every single seed here,
    # unconditionally, since argmax never consults the rng at all.
    picks = {select_reseed(survivors, random.Random(seed)).dev_fitness
             for seed in range(20)}
    assert picks - {top.dev_fitness}, f"never sampled a non-top survivor: {picks}"

    # (b) over many draws: every pick stays within the survivor set, AND
    # higher fitness is favored (not just "any of the three, uniformly") --
    # the weighting genuinely tracks fitness rather than being arbitrary.
    rng = random.Random(12345)
    counts = {low.dev_fitness: 0, mid.dev_fitness: 0, top.dev_fitness: 0}
    for _ in range(2000):
        picked = select_reseed(survivors, rng)
        assert picked in survivors
        counts[picked.dev_fitness] += 1
    assert counts[top.dev_fitness] > counts[mid.dev_fitness] > counts[low.dev_fitness]
