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
import shutil
from collections import Counter
from pathlib import Path

from pytest import approx

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness import loop as loop_mod
from nethackers.harness.archive import UNION, CellArchive
from nethackers.harness.loop import IterationResult, _pick_cell, run_loop
from nethackers.harness.metering import TokenUsage
from nethackers.harness.refs import Attempt
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


def test_hypothesis_of_returns_the_new_line_not_an_inherited_one(tmp_path):
    # The real bug: a worktree is copied from the parent elite, which ALREADY
    # carries a `# hypothesis:` from an earlier mutation (typically in an
    # early-sorting file like autoascend/agent.py). The mutator adds its NEW
    # hypothesis in some other file. _hypothesis_of(worktree, parent) must
    # report the NEW one -- diffing against the parent -- not the inherited
    # early-sort match the old first-in-sorted-order scan returned.
    from nethackers.harness.loop import _hypothesis_of
    parent = tmp_path / "parent"
    (parent / "autoascend").mkdir(parents=True)
    (parent / "autoascend" / "agent.py").write_text(
        "# hypothesis: inherited from an ancestor\nX = 1\n")
    worktree = tmp_path / "worktree"
    shutil.copytree(parent, worktree)                       # inherits agent.py's hypothesis
    (worktree / "autoascend" / "zzz_change.py").write_text(
        "# hypothesis: the change this mutation actually made\n")
    assert _hypothesis_of(worktree, parent) == "the change this mutation actually made"


def test_hypothesis_of_is_none_when_the_mutation_added_no_new_hypothesis(tmp_path):
    # If the mutation added no NEW hypothesis (edited code without one, or left
    # the tree carrying only inherited comments), the note must be honest --
    # None -- rather than echoing an ancestor's hypothesis as if it were tried.
    from nethackers.harness.loop import _hypothesis_of
    parent = tmp_path / "parent"
    (parent / "autoascend").mkdir(parents=True)
    (parent / "autoascend" / "agent.py").write_text(
        "# hypothesis: inherited only\nX = 1\n")
    worktree = tmp_path / "worktree"
    shutil.copytree(parent, worktree)
    (worktree / "bot.py").write_text("VERSION = 2\n")       # a change, but NO new hypothesis
    assert _hypothesis_of(worktree, parent) is None


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


class _ElitesHub(_FakeHub):
    """A hub that also serves per-identity elites for cold-start cell seeding.
    `by_identity` maps identity -> a champion entry; every fixture entry
    below carries owner='dev', matching every one of this file's run_loop
    calls that use `_ElitesHub` -- so every entry is trusted (select._trusted
    is an owner match only; there's no 'verified' tier)."""
    def __init__(self, by_identity):
        super().__init__()
        self._by = by_identity
    def elites(self, identity):
        e = self._by.get(identity)
        return [e] if e is not None else []


def _champion_fetch(version_by_digest):
    """A cold-start `fetch` that materializes each champion's tree on demand:
    the solution manifest plus a bot.py whose VERSION the fake fitness runner
    scores. Keyed by program_id -- an opaque id (never a real 'sha256:...'
    content digest), so select._resolve caches it via store.save_as and never
    hits a real git pull."""
    def fetch(entry, dest):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "nethackers.solution.json").write_text(json.dumps(
            {"schema": "nethackers.solution/v1", "name": "champ", "root": ".",
             "parents": [], "influences": [], "entrypoint": "bot.py"}))
        (dest / "bot.py").write_text(
            f"VERSION = {version_by_digest[entry['program_id']]}\n")
        return dest
    return fetch


def _spy_batches(monkeypatch):
    """Record (mounted-tree, identity-set) for every evaluate() the loop makes,
    delegating to the real evaluate. With iterations=0 the only calls are the
    cold-start evals, so the identity-sets are exactly what each starting elite
    was scored on."""
    seen: list[tuple[str, frozenset[str]]] = []
    real = loop_mod.evaluate
    def spy(tree, spec, image, **kw):
        seen.append((str(tree), frozenset(c for _s, c in spec.batch)))
        return real(tree, spec, image, **kw)
    monkeypatch.setattr(loop_mod, "evaluate", spy)
    return seen


def _spy_assemble(monkeypatch):
    """Record the `attempts` list passed to refs.assemble on every call (once
    per iteration), delegating to the real assemble so /refs/ is still built
    on disk. Lets a test inspect the actual Attempt objects the loop recorded
    (hypothesis/per_identity/overall/eval_json), not just their rendered text."""
    seen: list[list[Attempt]] = []
    real = loop_mod.refs.assemble
    def spy(dest, **kw):
        seen.append(list(kw["attempts"]))
        return real(dest, **kw)
    monkeypatch.setattr(loop_mod.refs, "assemble", spy)
    return seen


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


def test_on_state_resolves_union_parent_snapshot(tmp_path):
    """_emit's parent-snapshot guard checked `cell in archive.cells`, but a
    union-sampled iteration names cell="union" -- a key that lives in
    archive.union, never in archive.cells -- so the payload silently kept its
    blank defaults (parent_digest="") on every union-sampled iteration. A
    2-identity set with NO hub elites scores the seed on the full union at
    cold start, seeding archive.union immediately; rng=random.Random(0) is
    known to draw "union" as _pick_cell's very first pick for this label
    order (["wiz-elf-cha-mal", "wiz-orc-cha-mal", "union"], weights [1,1,2]).
    A flat fitness function ties the seed's own score everywhere, so the
    union cell never moves off the cold-start seed during the run -- the
    'mutating' snapshot must match it exactly."""
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    seed_tree = _seed_tree(tmp_path / "seed")
    tree_store = LocalTreeStore(tmp_path / "store")
    cold_seed_digest = tree_store.save(seed_tree)  # same digest run_loop computes
    states: list[dict] = []
    run_loop(
        objective=f"{a},{b}", seed_tree=seed_tree, tree_store=tree_store,
        operator=_ImprovingOperator(), hub=_FakeHub(), image="img:dev", token="t",
        owner="dev", iterations=1, now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.5),  # flat: nothing ever beats the cold-start seed
        workdir=tmp_path / "work", rng=random.Random(0),
        on_state=states.append)
    mut = next(s for s in states if s["phase"] == "mutating")
    assert mut["cell"] == UNION                        # confirms this run drew the union cell
    assert mut["parent_digest"] == cold_seed_digest     # NOT "" -- the pre-fix blank default
    assert mut["parent_dev"] == approx(0.5)             # archive.union.score at cold start
    assert mut["parent_means"] == approx({a: 0.5, b: 0.5})


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
    # cold start (no hub elites) scores the seed on the FULL union in one
    # eval, so the union cell is already seeded (0.2) before iteration 1; this
    # child's union mean (0.9+0.1)/2=0.5 beats it too -- deterministically,
    # regardless of which cell _pick_cell drew as the mutation parent.
    assert win.improved == ["wiz-elf-cha-mal", "union"]


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
    # With a seeded rng and a 2-identity set, the mutated cell is _pick_cell's
    # weighted draw; the "mutating" state names it. Cold start here has NO hub
    # elites, so the seed is scored on the full union in one eval and the
    # union cell is already seeded before iteration 1 -- so the draw is over
    # all THREE labels (identities + "union"), not just the two identities.
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
    assert mut["cell"] in ("wiz-elf-cha-mal", "wiz-orc-cha-mal", UNION)


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


# -- amendment: a RUN-GLOBAL attempt history (capped at _ATTEMPT_REFS_CAP, not
# per-cell, not cross-run) carried into the mutator via /refs/attempts.md (a
# per-identity scores table) and /refs/attempts/<n>/ (the real code, with its
# own eval.json) -- never in the brief itself -- so it avoids re-deriving a
# dead mutation.

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
    # The brief itself no longer carries attempt history (brief.py dropped
    # `attempts` entirely) -- the history now lives in /refs/attempts.md,
    # rebuilt fresh each iteration from the run-global `attempts` list.
    op = _HypothesisOperator()
    workdir = tmp_path / "work"
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=_FakeHub(),
        image="img:dev", token="t", owner="dev", iterations=3,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),  # every mutant regresses
        workdir=workdir, rng=random.Random(0))
    assert len(op.briefs) == 3
    for brief in op.briefs:
        assert "try tactic" not in brief and "iter-1" not in brief   # no attempt leaked in

    refs0 = (workdir / "refs-0" / "attempts.md").read_text()
    assert "(none yet)" in refs0                        # first iteration: no history yet

    refs1 = (workdir / "refs-1" / "attempts.md").read_text()
    assert "| 1 |" in refs1                              # iter 2 sees iter 1's attempt...
    assert "try tactic 1" in refs1                       # ...including its real hypothesis...
    assert "0.100" in refs1                              # ...and its per-identity score

    refs2 = (workdir / "refs-2" / "attempts.md").read_text()
    assert "| 1 |" in refs2 and "| 2 |" in refs2          # run-global, not per-cell


def test_attempts_are_capped_at_three(tmp_path):
    # Every mutant regresses -> 5 straight "improved no cell" iterations, each
    # recorded as an Attempt. Unlike the design this replaces (a brief-text
    # history, uncapped across the whole run), the run-global Attempt history
    # is CAPPED at _ATTEMPT_REFS_CAP (3) -- both /refs/attempts.md and
    # /refs/attempts/ must never hold more than the 3 most-recent attempts.
    workdir = tmp_path / "work"
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_AlwaysRejectingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=5,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),
        workdir=workdir, rng=random.Random(0))
    # refs-k is built from the attempts recorded by the k PRIOR iterations --
    # never more than 3 trees, even once more than 3 have been tried.
    expected = [0, 1, 2, 3, 3]
    for k, want in enumerate(expected):
        refs = workdir / f"refs-{k}"
        attempts_dir = refs / "attempts"
        count = len(list(attempts_dir.iterdir())) if attempts_dir.exists() else 0
        assert count == want, f"refs-{k}: expected {want} attempt tree(s), got {count}"
        rows = [ln for ln in (refs / "attempts.md").read_text().splitlines()
                if ln.startswith("|") and ln.split("|")[1].strip().isdigit()]
        assert len(rows) == want
    # by the last iteration's /refs/ (built from attempts 1-4), the cap has
    # actually evicted the oldest -- "1" is gone, "2"/"3"/"4" remain.
    final_md = (workdir / "refs-4" / "attempts.md").read_text()
    assert "| 1 |" not in final_md
    assert "| 2 |" in final_md and "| 3 |" in final_md and "| 4 |" in final_md


def test_maplites_provisions_recent_rejected_attempt_trees_into_refs(tmp_path):
    # Restore the /refs/attempts channel: each iteration's mutator can inspect
    # the CODE of recent rejected attempts (diff/read it), not just a score.
    # Every mutant here regresses ("improved no cell"), so by the 5th
    # iteration /refs/attempts holds the 3 most-recent rejected trees -- capped
    # and recency-ordered, so the oldest (label "1") is evicted. Folder labels
    # are the plain iteration number ("2"/"3"/"4"), not "iter-2"/... -- that
    # prefix was a brief-text convention that no longer exists.
    workdir = tmp_path / "work"
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_AlwaysRejectingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=5,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),
        workdir=workdir, rng=random.Random(0))
    attempts = workdir / "refs-4" / "attempts"          # /refs for the 5th iteration (k=4)
    labels = sorted(p.name for p in attempts.iterdir())
    assert labels == ["2", "3", "4"]                    # 3 most-recent; "1" evicted by the cap
    # the copied tree is the real rejected mutant, not a stub: "4" == VERSION 4
    assert (attempts / "4" / "bot.py").read_text().strip() == "VERSION = 4"
    # each kept attempt carries its own per-seed eval.json (the real score,
    # not just the code) -- the richer signal the redesign adds.
    for label in labels:
        assert (attempts / label / "eval.json").exists()
    context = (workdir / "refs-4" / "CONTEXT.md").read_text()
    assert "attempts.md" in context and "attempts/<n>/" in context


def test_maplites_first_iteration_has_no_attempt_trees(tmp_path):
    # The very first mutation has no prior attempts -> /refs/attempts is absent
    # (refs.assemble writes the section only when there is something to show).
    workdir = tmp_path / "work"
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_AlwaysRejectingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=1,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 if v == 0 else 0.1),
        workdir=workdir, rng=random.Random(0))
    assert not (workdir / "refs-0" / "attempts").exists()


class _InheritedHypothesisOperator:
    """Call 1 wins and leaves a hypothesis in an EARLY-sorting file
    (autoascend/agent.py); later calls INHERIT it (copied from the elite) and
    add their OWN hypothesis in a LATE-sorting file (autoascend/zzz.py) without
    touching the inherited one. Reproduces the field bug: the note must report
    each mutation's OWN (late-file) hypothesis, not the inherited early-file
    line the old first-in-sorted-order scan returned for every descendant."""
    def __init__(self): self.briefs: list[str] = []
    def run(self, worktree, brief, *, refs=None, on_line=None, stop=None):
        from nethackers.harness.operator import OperatorResult
        self.briefs.append(brief)
        n = len(self.briefs)
        wt = Path(worktree)
        (wt / "autoascend").mkdir(exist_ok=True)
        if n == 1:
            (wt / "autoascend" / "agent.py").write_text("# hypothesis: inherited early idea\n")
        (wt / "bot.py").write_text(f"VERSION = {n}\n")
        (wt / "autoascend" / "zzz.py").write_text(f"# hypothesis: new idea {n}\n")
        return OperatorResult(backend="fake", usage=TokenUsage(1, 2, 3, 4),
                              stopped_reason="completed")


def test_brief_note_reports_the_mutations_own_hypothesis_not_an_inherited_one(tmp_path):
    # The reported bug, end to end: iter 1 wins and its hypothesis lands in
    # agent.py; iters 2+ inherit that comment and add their own in zzz.py. The
    # Attempt recorded for iter 2 (surfaced in iter 3's /refs/attempts.md) must
    # carry iter 2's OWN "new idea 2", not the inherited "inherited early
    # idea" (which the pre-fix scan returned). _hypothesis_of's diff-against-
    # parent logic is unchanged -- this proves its output lands correctly in
    # the new /refs/ location.
    op = _InheritedHypothesisOperator()
    workdir = tmp_path / "work"
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=op, hub=_FakeHub(),
        image="img:dev", token="t", owner="dev", iterations=3,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: [0.2, 0.9, 0.1, 0.1][v]),   # v1 wins; v2,v3 don't
        workdir=workdir, rng=random.Random(0))
    refs2 = (workdir / "refs-2" / "attempts.md").read_text()   # /refs/ for the 3rd mutation
    row2 = next(ln for ln in refs2.splitlines() if ln.startswith("| 2 |"))
    assert "new idea 2" in row2                  # its OWN hypothesis...
    assert "inherited early idea" not in row2    # ...not the inherited one


def test_smoke_gate_reject_not_in_attempts(tmp_path, monkeypatch):
    # A child that fails the smoke gate has no dev score -- it must not become
    # an Attempt: no /refs/attempts/<n>/ tree, no /refs/attempts.md row for
    # it. (Registered AND rejected children that DO reach a dev eval both
    # become Attempts -- see test_evaluated_attempt_records_scores and
    # test_maplites_provisions_recent_rejected_attempt_trees_into_refs.)
    monkeypatch.setattr(loop_mod, "passes_gate",
                        lambda *a, **k: (False, "smoke episode did not complete"))
    workdir = tmp_path / "work"
    results = run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=2,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=workdir,
        rng=random.Random(0))
    assert results[0].reason == "gate:smoke episode did not complete"
    refs1 = workdir / "refs-1"                          # the 2nd iteration's /refs/
    assert not (refs1 / "attempts").exists()
    assert "(none yet)" in (refs1 / "attempts.md").read_text()


def test_evaluated_attempt_records_scores(tmp_path, monkeypatch):
    # After an evaluated (dev-scored) iteration -- registered here -- its
    # Attempt must carry real per-identity scores, a real overall, and the
    # actual per-seed results, not placeholder/empty values.
    seen = _spy_assemble(monkeypatch)
    run_loop(
        objective="val-dwa-law-fem", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=2,
        now_fn=lambda: "2026-08-26T00:00:00Z",
        runner=_fitness_runner(lambda v: 0.2 + 0.1 * v), workdir=tmp_path / "work",
        rng=random.Random(0))
    attempt = seen[1][0]           # iter 2's /refs/ sees iter 1's (registered) Attempt
    assert attempt.label == "1"
    assert attempt.per_identity == approx({"val-dwa-law-fem": 0.3})
    assert attempt.overall == approx(0.3)
    assert attempt.eval_json and json.loads(attempt.eval_json)   # non-empty, valid JSON


# -- cold start: each cell is seeded on ITS OWN identity, not the full union --

def test_coldstart_scores_each_champion_on_its_own_identities(tmp_path, monkeypatch):
    # 3-identity set, every identity covered by a champion. Champion A is the
    # hub elite for TWO identities, champion B for the third. Each champion must
    # be scored once on ONLY the identities it owns -- never on the full union
    # (the old P*N blowup) -- and with every cell covered, the seed is not
    # scored at all. So the ONLY evals are the two champion sub-unions.
    a, b, c = "mon-hum-cha-mal", "mon-hum-law-mal", "mon-hum-neu-mal"
    champ_a = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/a", "commit": "11"}}
    champ_b = {"program_id": "github.com/t/b@22", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/b", "commit": "22"}}
    seen = _spy_batches(monkeypatch)
    run_loop(
        objective=f"{a},{b},{c}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_ElitesHub({a: champ_a, b: champ_a, c: champ_b}),
        image="img:dev", token="t", owner="dev", iterations=0,
        now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7, 9: 0.9}[v]),
        fetch=_champion_fetch({"github.com/t/a@11": 5, "github.com/t/b@22": 9}),
        workdir=tmp_path / "work")
    identity_sets = sorted((s for _t, s in seen), key=len)
    assert identity_sets == [
        frozenset({c}),          # champion B on its one identity
        frozenset({a, b}),       # champion A on its two -- NOT the full union
    ]


def test_coldstart_fills_each_cell_with_its_own_champion(tmp_path):
    # champion A is the elite for id1, champion B for id2. Because each is scored
    # ONLY on its own identity, A can't overwrite id2 (nor B id1): each cell ends
    # up owned by its champion. (On the old full-union path both champions scored
    # every identity, so the higher one took both cells.)
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    champ_a = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/a", "commit": "11"}}
    champ_b = {"program_id": "github.com/t/b@22", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/b", "commit": "22"}}
    states = []
    run_loop(
        objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_ElitesHub({a: champ_a, b: champ_b}), image="img:dev", token="t",
        owner="dev", iterations=0, now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7, 9: 0.9}[v]),
        fetch=_champion_fetch({"github.com/t/a@11": 5, "github.com/t/b@22": 9}),
        workdir=tmp_path / "work", on_state=states.append)
    cold = next(s for s in states if s["phase"] == "cold-start")
    cells = {cell["identity"]: cell["digest"] for cell in cold["cells"]}
    assert cells[a] == "github.com/t/a@11"   # champion A owns its cell
    assert cells[b] == "github.com/t/b@22"   # champion B owns its cell


def test_coldstart_seeds_only_championless_cells(tmp_path, monkeypatch):
    # champion owns id1; id2 has NO hub elite -> the seed is scored on id2 ONLY,
    # never the full union.
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    champ = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
             "reference": {"repo": "github.com/t/a", "commit": "11"}}
    store = LocalTreeStore(tmp_path / "store")
    seed_tree = _seed_tree(tmp_path / "seed")
    seed_path = store.path(store.save(seed_tree))
    seen = _spy_batches(monkeypatch)
    run_loop(
        objective=f"{a},{b}", seed_tree=seed_tree, tree_store=store,
        operator=_ImprovingOperator(), hub=_ElitesHub({a: champ}),
        image="img:dev", token="t", owner="dev", iterations=0,
        now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7}[v]),
        fetch=_champion_fetch({"github.com/t/a@11": 5}), workdir=tmp_path / "work")
    by_tree = {t: s for t, s in seen}
    assert by_tree[str(seed_path)] == frozenset({b})          # seed scored on id2 ONLY
    assert frozenset({a, b}) not in [s for _t, s in seen]     # never the full union


def test_coldstart_base_dev_is_the_frontier_mean(tmp_path):
    # champion owns id1 (scores 0.7); id2 is championless -> seeded at 0.2.
    # base_dev is the frontier mean (0.7 + 0.2) / 2 = 0.45, NOT the seed's union
    # mean (0.2, what the old full-union seed eval reported).
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    champ = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
             "reference": {"repo": "github.com/t/a", "commit": "11"}}
    baseline = []
    run_loop(
        objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_ElitesHub({a: champ}), image="img:dev", token="t", owner="dev",
        iterations=0, now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7}[v]),
        fetch=_champion_fetch({"github.com/t/a@11": 5}), workdir=tmp_path / "work",
        on_iteration=lambda i, r: baseline.append(r))
    assert baseline[0].reason == "baseline"
    assert baseline[0].dev_fitness == approx(0.45)


def test_coldstart_baseline_when_every_cell_has_a_champion(tmp_path):
    # Every identity has a champion -> the seed eval is skipped entirely. The
    # baseline must still be emitted (dev_fitness = frontier mean), never crash
    # on a seed eval that didn't run (the old causes=_causes(seed_ev.results)
    # would reference an unbound seed_ev here).
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    champ_a = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/a", "commit": "11"}}
    champ_b = {"program_id": "github.com/t/b@22", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/b", "commit": "22"}}
    baseline = []
    run_loop(
        objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_ElitesHub({a: champ_a, b: champ_b}), image="img:dev", token="t",
        owner="dev", iterations=0, now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7, 9: 0.9}[v]),
        fetch=_champion_fetch({"github.com/t/a@11": 5, "github.com/t/b@22": 9}),
        workdir=tmp_path / "work", on_iteration=lambda i, r: baseline.append(r))
    assert baseline[0].reason == "baseline"
    assert baseline[0].dev_fitness == approx(0.8)   # (0.7 + 0.9) / 2, no seed eval


def test_coldstart_warm_cell_mutates_without_error(tmp_path):
    # The branch's headline scenario: a warm-started cell (seeded from a hub
    # champion, so its dev_evidence spans ONLY that champion's identity) must
    # mutate cleanly on iteration 1. The mutated cell's SUBSET per-identity means
    # (parent_means) flow into aggregate.regressions and the brief against a
    # FULL-union child eval -- a shape that couldn't arise under the old
    # full-union cold-start. Proves the D2 subset doesn't trip the unchanged
    # regression/brief path (no silent 'error:' iteration).
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    champ_a = {"program_id": "github.com/t/a@11", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/a", "commit": "11"}}
    champ_b = {"program_id": "github.com/t/b@22", "score": 0.99, "owner": "dev",
               "reference": {"repo": "github.com/t/b", "commit": "22"}}
    results = run_loop(
        objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
        tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
        hub=_ElitesHub({a: champ_a, b: champ_b}), image="img:dev", token="t",
        owner="dev", iterations=1, now_fn=lambda: "2026-08-28T00:00:00Z",
        runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7, 9: 0.9}.get(v, 0.95)),
        fetch=_champion_fetch({"github.com/t/a@11": 5, "github.com/t/b@22": 9}),
        workdir=tmp_path / "work", rng=random.Random(0))
    assert not results[0].reason.startswith("error")   # subset parent_means: regressions+brief ok
    assert results[0].registered is True               # the warm cell mutated and improved a cell


# -- Task 3: per-cell origin labels ("hub"/"seed"/"run") + the AutoAscend
# per-identity baseline floor, both emitted on the cold-start (and later
# registered) on_state payload so the monitor can label each cell's elite.

def test_coldstart_emits_origins_and_baseline(tmp_path):
    a = "wiz-elf-cha-mal"
    b = "wiz-orc-cha-mal"
    champ_a = {"program_id": "github.com/t/a@11", "score": 0.9, "owner": "clyde",
               "reference": {"repo": "github.com/t/a", "commit": "11"}}
    class _Hub(_ElitesHub):
        def baseline(self):   # real /baseline shape: dict, per_identity -> {"progression"}
            return {"owner": "autoascend", "overall": 0.22,
                    "per_identity": {a: {"progression": 0.20, "episodes": 1},
                                     b: {"progression": 0.25, "episodes": 1}}}
    states = []
    run_loop(objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
             tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
             hub=_Hub({a: champ_a}),  # only A has a champion; B falls to the seed
             image="img:dev", token="t", owner="dev", iterations=0,
             now_fn=lambda: "2026-09-06T00:00:00Z",
             runner=_fitness_runner(lambda v: {0: 0.2, 5: 0.7}[v]),
             fetch=_champion_fetch({"github.com/t/a@11": 5}),
             workdir=tmp_path / "work", on_state=states.append)
    cold = next(s for s in states if s["phase"] == "cold-start")
    assert cold["origins"]["github.com/t/a@11"] == {
        "kind": "hub", "handle": "clyde", "sha": "11",
        "repo": "github.com/t/a", "iteration": None}
    assert cold["aa_baseline"][b] == 0.25   # AutoAscend floor for the championless cell


def test_registered_child_gets_run_origin(tmp_path):
    a = "wiz-elf-cha-mal"
    states = []
    run_loop(objective=a, seed_tree=_seed_tree(tmp_path / "seed"),
             tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
             hub=_FakeHub(), image="img:dev", token="t", owner="dev", iterations=1,
             now_fn=lambda: "2026-09-06T00:00:00Z", rng=random.Random(0),
             runner=_fitness_runner(lambda v: {0: 0.2, 1: 0.8}[v]),
             workdir=tmp_path / "work", on_state=states.append)
    reg = next(s for s in states if s["phase"] == "registered")
    run_origins = {d: o for d, o in reg["origins"].items() if o["kind"] == "run"}
    assert run_origins and next(iter(run_origins.values()))["iteration"] == 1


def test_coldstart_baseline_read_survives_a_malformed_shape(tmp_path):
    # hub.baseline() can succeed (raise nothing) but still return a malformed
    # shape -- a None per-identity entry, or an entry whose "progression" is
    # None rather than a number. _baseline_floor's best-effort try/except must
    # cover the WHOLE parse (not just the hub.baseline() call itself), so a
    # malformed shape degrades to {} instead of crashing run_loop.
    a, b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    class _Hub(_ElitesHub):
        def baseline(self):
            return {"per_identity": {a: None, b: {"progression": None}}}
    states = []
    run_loop(objective=f"{a},{b}", seed_tree=_seed_tree(tmp_path / "seed"),
             tree_store=LocalTreeStore(tmp_path / "store"), operator=_ImprovingOperator(),
             hub=_Hub({}), image="img:dev", token="t", owner="dev", iterations=0,
             now_fn=lambda: "2026-09-07T00:00:00Z",
             runner=_fitness_runner(lambda v: 0.2),
             workdir=tmp_path / "work", on_state=states.append)
    cold = next(s for s in states if s["phase"] == "cold-start")
    assert cold["aa_baseline"] == {}   # malformed entries dropped, not a crash


# -- _pick_cell: weighted parent draw over the archive's cells. Each identity
# weight 1, the union cell weight 2 -- but only once a full-coverage program
# has filled it; before that the draw stays uniform over identities.

def _ev2(means):  # local factory: one episode per identity
    results = tuple(
        TrajectoryResult(trajectory_id=i, status="completed", progress=v, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=c, milestone=None)
        for i, (c, v) in enumerate(means.items()))
    return Evidence.from_results(solution_digest="sha256:x",
                                 objective=Objective(character=None, seed_set="s"),
                                 evaluator_image="img", results=results, created_at="t")


def test_pick_cell_uniform_before_union_exists(tmp_path):
    arc = CellArchive(["a", "b"])
    arc.insert("a0", tmp_path / "a0", _ev2({"a": 0.3}))   # partial -> union stays None
    arc.insert("b0", tmp_path / "b0", _ev2({"b": 0.3}))   # partial -> union stays None
    assert arc.union is None
    label, cell = _pick_cell(random.Random(0), arc)
    assert label in ("a", "b")
    assert cell is arc.cell(label)


def test_pick_cell_weights_union_2x(tmp_path):
    arc = CellArchive(["a", "b"])
    arc.insert("gen", tmp_path / "gen", _ev2({"a": 0.5, "b": 0.5}))  # full -> union set
    assert arc.union is not None
    rng = random.Random(0)
    counts = Counter(_pick_cell(rng, arc)[0] for _ in range(6000))
    # weights: a=1, b=1, union=2  -> union share 2/4 = 0.5
    assert 0.45 < counts[UNION] / 6000 < 0.55
    assert _pick_cell(random.Random(0), arc)  # returns without error
