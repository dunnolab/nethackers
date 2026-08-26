# tests/test_harness_loop.py
#
# The island/champion/validation-gate tests were REPLACED here: the loop is now
# MAP-Elites over a per-identity CellArchive (Task B4). Removed with them were
# every island seeding / periodic-reset / select_reseed / influence-pool /
# client-side register-slice test, and the /refs `attempts/` tree tests (the
# run's attempt history is now carried as textual NOTES in the brief, not as
# copied trees -- see the amendment tests at the bottom).
import json
import random
from pathlib import Path

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


def test_hypothesis_extracted_from_worktree(tmp_path):
    # The run-global attempt note reuses the mutator's real `# hypothesis:` line
    # (this pure helper greps the worktree's Python for it), so a later
    # iteration's brief sees the actual idea tried, not just the outcome.
    from nethackers.harness.loop import _hypothesis_of
    (tmp_path / "bot.py").write_text("x = 1\n")
    (tmp_path / "autoascend").mkdir()
    (tmp_path / "autoascend" / "logic.py").write_text(
        "def f():\n    return 1  # hypothesis: heal earlier at <1/2 HP\n")
    assert _hypothesis_of(tmp_path) == "heal earlier at <1/2 HP"
    assert _hypothesis_of(tmp_path / "autoascend") is not None  # dir walk


class _FakeHub:
    """Records each registration's solution_digest. Has no `.elites`, so the
    cold-start per-identity overlay degrades to empty (the loop swallows the
    AttributeError) -- these tests exercise the loop, not hub cell-seeding."""
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
    gate path (rejected: the worktree is untouched, so identical to parent)."""
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        return OperatorResult(backend="codex", usage=TokenUsage(), stopped_reason="killed")


class _AlwaysRejectingOperator:
    """Every call mutates to a distinct, never-seen-before VERSION but never
    beats the seed's baseline fitness -- every iteration is 'improved no cell',
    so the run-global attempt history keeps accumulating across the whole run."""
    def __init__(self): self.seen = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.seen.append(refs)
        (Path(worktree)/"bot.py").write_text(f"VERSION = {len(self.seen)}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


def _fitness_runner(progress_by_version):
    """Fake Docker runner: reads the mounted bot's VERSION, scores by table."""
    def fake(cmd, check):
        sol = next(v.removesuffix(":/sol:ro") for v in cmd if v.endswith(":/sol:ro"))
        version = int(Path(sol, "bot.py").read_text().split("=")[1].splitlines()[0])
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
        version = int(Path(sol, "bot.py").read_text().split("=")[1].splitlines()[0])
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
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40})
    assert results[0].registered is True
    assert results[0].improved == ["val-dwa-law-fem"]
    assert len(hub.registered) == 1
    assert results[0].hub_reason is None   # reached the hub -- nothing to explain


def test_loop_win_without_publisher_is_a_local_elite(tmp_path):
    # No `publish` hook -> the win improves the cell locally but is never
    # registered against the hub (no synthetic, unfetchable reference).
    hub = _FakeHub()
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="dev-token", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert results[0].registered is True   # improved a cell (the MAP-Elites signal)
    assert hub.registered == []            # but nothing went to the hub
    assert results[0].hub_reason == "local-only: not published (no gh publisher / dev owner)"


class _FailingHub:
    """register always fails (hub down / a 400). An improving child must still
    enter the archive -- never discarded over a hub-side failure."""
    def register(self, *, token, reference, manifest, evidence):
        raise RuntimeError("hub 400")


def test_loop_keeps_win_local_when_register_fails(tmp_path):
    # iter1 (v1=0.40) beats the seed (v0=0.20) -> improves the cell, but the hub
    # register raises. The child must still become the cell's elite
    # (registered=True, not an error), proven by iter2 (v2=0.30) improving no
    # cell against it -- the reason is now MAP-Elites' "no-cell-improved".
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FailingHub(), image="img:dev", token="dev-token", owner="dev", iterations=2,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: [0.20, 0.40, 0.30][v]), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40})
    assert results[0].registered is True         # win kept despite the register 400
    assert results[0].reason == "registered"     # not "error:hub 400"
    assert results[0].hub_reason == "local-only: hub error — hub 400"
    assert results[1].registered is False         # cell advanced to 0.40...
    assert results[1].reason == "no-cell-improved"   # ...so iter2's 0.30 improves nothing


class _AuthFailingHub:
    """register always raises AuthError (an expired stored token whose
    refresh_token is dead, or absent) -- same local-kept contract as
    _FailingHub, but the loop must add owner context to the generic message."""
    def register(self, *, token, reference, manifest, evidence):
        from nethackers.hubclient.auth import AuthError
        raise AuthError("hub token expired and could not refresh — run `nethackers login`")


def test_loop_win_records_auth_failure_reason_with_owner_context(tmp_path):
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_AuthFailingHub(), image="img:dev", token="stale-token", owner="sam",
        iterations=1, now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        publish=lambda wt: {"repo": "github.com/sam/nethacker", "commit": "a" * 40})
    assert results[0].registered is True   # child still enters the archive
    assert results[0].reason == "registered"
    assert results[0].hub_reason == (
        "local-only: auth failed for run owner 'sam' — "
        "hub token expired and could not refresh — run `nethackers login`")


def test_loop_emits_hub_reason_on_the_registered_state_payload(tmp_path):
    # The monitor's ledger reads hub_reason off the "registered" on_state
    # payload (harness.loop._emit), not off IterationResult directly.
    states = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_state=states.append)   # no publisher -> local-only
    registered = [s for s in states if s["phase"] == "registered"]
    assert len(registered) == 1
    assert registered[0]["hub_reason"] == "local-only: not published (no gh publisher / dev owner)"


def test_loop_records_faithful_usage(tmp_path):
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
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
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work")  # flat: no gain
    assert results[0].registered is False
    assert hub.registered == []   # no publisher -> nothing registered either


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
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    assert len(results) == 1
    assert results[0].registered is False
    assert results[0].reason.startswith("operator-error")
    assert hub.registered == []


def test_loop_circuit_breaker_stops_after_consecutive_raising_operators(tmp_path):
    """A RAISING operator.run (e.g. a missing/renamed CLI binary) must trip the
    breaker with backoff -- otherwise a persistently-broken operator fast-spins
    the whole `iterations` budget."""
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_RaisingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=10,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        max_consecutive_errors=3, sleep=slept.append)
    assert len(results) == 3
    assert all(r.reason.startswith("operator-error") for r in results)
    assert slept == [1, 2]   # backoff after error 1 and 2; error 3 breaks (no sleep)


def test_loop_non_operator_raise_does_not_trip_the_operator_breaker(tmp_path, monkeypatch):
    """A raise from an UNRELATED step (here: the gate) is not an operator-error
    -- it keeps falling through to the generic outer `except` (reason "error:")
    and must NOT increment the operator-breaker's consecutive_errors."""
    def _boom(*a, **k):
        raise RuntimeError("gate blew up")
    monkeypatch.setattr(loop_mod, "passes_gate", _boom)
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=3,
        now_fn=lambda: "2026-08-10T00:00:00Z",
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
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work",   # flat: no gain
        max_consecutive_errors=3, sleep=lambda _s: None)
    assert len(results) == 4
    assert all(not r.registered for r in results)


def test_loop_killed_operator_does_not_trip_the_breaker(tmp_path):
    # A killed operator (stopped_reason="killed") returns normally and falls
    # through to the normal gate path (rejected: the worktree is untouched, so
    # identical to parent) without ever incrementing the breaker.
    slept: list[float] = []
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_KilledOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=5,
        now_fn=lambda: "2026-08-10T00:00:00Z",
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
    """MAP-Elites: cold-start scores the seed into every cell (generation 0);
    the per-cell parent snapshot appears on the 'mutating' state, whose
    parent_digest is the seeded cell's elite (the seed digest at iter 1)."""
    hub = _FakeHub()
    seed_tree = _seed_tree(tmp_path / "seed")
    tree_store = LocalTreeStore(tmp_path / "store")
    cold_seed_digest = tree_store.save(seed_tree)  # same digest run_loop computes
    states: list[dict] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=seed_tree,
        tree_store=tree_store, operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="t", owner="o", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_state=states.append)
    phases = {s["phase"]: s for s in states}

    cold = phases["cold-start"]
    assert cold["generation"] == 0
    assert cold["cells"][0]["digest"] == cold_seed_digest   # the seed fills the cell

    mut = phases["mutating"]
    assert set(mut) >= {"parent_digest", "parent_dev", "generation", "cell"}
    assert mut["generation"] == 1                     # iteration 1 (advances per attempt)
    assert mut["cell"] == "val-dwa-law-fem"           # the mutated cell
    assert mut["parent_digest"] == cold_seed_digest   # mutated from the seed elite
    assert mut["parent_dev"] == cold["best_dev"]      # == the seed's score


def test_iteration_result_stopped_reason_defaults_to_none():
    assert IterationResult(False, "baseline").stopped_reason is None


def test_on_iteration_fires_for_baseline_and_each_iteration(tmp_path):
    seen: list[tuple[int, str]] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="o", iterations=1,
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
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_log=lambda tag, line: logs.append((tag, line)))
    # the brief heads the iteration log (so a reader sees the instruction),
    # then the operator's own stream lines follow.
    assert logs and logs[0][0] == "iter 1/1" and "nethackers_brief" in logs[0][1]
    assert any(tag == "iter 1/1" and "editing" in line for tag, line in logs)


# -- generalist (set) objectives: MAP-Elites keeps a one-identity specialist,
# and _emit carries the live per-identity parent snapshot.

def test_maplites_keeps_a_one_identity_improver(tmp_path):
    # A set objective. The child improves ONLY wiz-elf (0.2 -> 0.9) and drops
    # wiz-orc (0.2 -> 0.1). Under the OLD average gate this was discarded;
    # MAP-Elites must KEEP it in wiz-elf's cell (a retained specialist) and
    # report registered=True with improved == ["wiz-elf-cha-mal"].
    def progress(version, character):
        if version == 0:
            return 0.2
        return 0.9 if character == "wiz-elf-cha-mal" else 0.1
    results = run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal",
        seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner_by_character(progress), workdir=tmp_path / "work",
        rng=random.Random(0))
    win = next(r for r in results if r.improved)
    assert win.registered is True
    assert win.improved == ["wiz-elf-cha-mal"]


def test_maplites_registers_every_scored_program(tmp_path):
    # register-all: even a program that improves NO cell is pushed + registered.
    hub = _FakeHub()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=hub, image="img:dev", token="t", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5), workdir=tmp_path / "work",   # flat: no cell improves
        publish=lambda wt: {"repo": "github.com/dev/nethacker", "commit": "a" * 40},
        rng=random.Random(0))
    assert len(hub.registered) == 1   # registered despite improving no cell


def test_maplites_picks_a_random_cell(tmp_path):
    # With a seeded rng and a 2-identity set, the mutated cell is the rng's
    # choice; the "mutating" state names it.
    states = []
    run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal",
        seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        on_state=states.append, rng=random.Random(0))
    mut = next(s for s in states if s["phase"] == "mutating")
    assert mut["cell"] in ("wiz-elf-cha-mal", "wiz-orc-cha-mal")


def test_loop_registers_regressions_on_a_per_identity_drop(tmp_path):
    """The union mean can rise (some cell improves) while another member
    identity of a set objective drops relative to the mutated cell's parent --
    the registered IterationResult must carry that regression, naming the
    dropped build (spec decision C §5/§9)."""
    def progress(version, character):
        if version == 0:  # cold-start seed: both builds tie at 0.5
            return 0.5
        # the winning child: wiz-elf rises, wiz-orc drops below the parent's 0.5
        return 0.9 if character == "wiz-elf-cha-mal" else 0.3

    results = run_loop(
        objective="wiz-elf-cha-mal,wiz-orc-cha-mal", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner_by_character(progress), workdir=tmp_path / "work",
        rng=random.Random(0))

    win = next(r for r in results if r.improved)
    assert win.registered is True
    assert win.regressions  # non-empty: at least one build dropped
    assert win.regressions[0][0] == "wiz-orc-cha-mal"  # names the dropped build
    assert win.regressions[0][1] < 0                   # a negative delta


def test_loop_no_regressions_for_a_single_identity_objective(tmp_path):
    # A size-1 identity set -> regressions(parent, child) is [] when the one
    # identity didn't drop -> regressions stays None (never an empty list).
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="dev-token", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-10T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work")
    win = results[-1]
    assert win.registered is True
    assert win.regressions is None


# -- amendment: a RUN-GLOBAL attempt history (uncapped, not per-cell, not
# cross-run) carried into the mutator's brief as NOTES, so it avoids
# re-deriving a dead mutation. Notes only -- the code tree is never copied.

class _HypothesisOperator:
    """Records the brief handed to each call, and writes a distinct VERSION
    plus a `# hypothesis:` comment (in a separate file so the fake runner's
    `VERSION = n` parse is unaffected) -- so a later iteration's brief can be
    checked for earlier attempts' ids AND their hypotheses."""
    def __init__(self): self.briefs: list[str] = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.briefs.append(brief)
        n = len(self.briefs)
        (Path(worktree) / "bot.py").write_text(f"VERSION = {n}\n")
        (Path(worktree) / "strategy.py").write_text(f"# hypothesis: try tactic {n}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


def test_maplites_brief_carries_earlier_attempt_notes(tmp_path):
    op = _HypothesisOperator()
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=_FakeHub(),
        image="img:dev", token="t", owner="dev", iterations=3,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),  # every mutant regresses
        workdir=tmp_path / "work", rng=random.Random(0))
    assert len(op.briefs) == 3
    assert "iter-1" not in op.briefs[0]                 # first iteration: no history yet
    assert "iter-1" in op.briefs[1]                     # iter 2 sees iter 1's attempt...
    assert "try tactic 1" in op.briefs[1]               # ...including its real hypothesis
    assert "improved no cell" in op.briefs[1]           # ...and its outcome
    assert "iter-1" in op.briefs[2] and "iter-2" in op.briefs[2]   # run-global, not per-cell


def _brief_for(tag: str, logs: list[tuple[str, str]]) -> str:
    for t, line in logs:
        if t == tag and "nethackers_brief" in line:
            return json.loads(line)["text"]
    raise AssertionError(f"no brief logged for {tag}")


def test_maplites_attempt_history_is_uncapped_across_the_run(tmp_path):
    # Every mutant regresses -> 5 straight "improved no cell" iterations. The
    # run-global history is UNCAPPED (unlike the old <=3 per-island cap), so
    # iteration 5's brief still carries a note for all four earlier attempts.
    logs: list[tuple[str, str]] = []
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_AlwaysRejectingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=5,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),
        workdir=tmp_path / "work", rng=random.Random(0),
        on_log=lambda tag, line: logs.append((tag, line)))
    brief5 = _brief_for("iter 5/5", logs)
    for i in range(1, 5):
        assert f"iter-{i}" in brief5   # all four earlier attempts, none dropped
