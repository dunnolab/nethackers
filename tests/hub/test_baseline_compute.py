"""Tests for baseline_compute: run AutoAscend (injected fake evaluate, no
Docker/arena) on an ObjectiveSpec and store the atoms in baseline_atoms, marked
owner/solution="autoascend", tier="baseline", isolated from participant atoms."""

from __future__ import annotations

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.baseline_compute import compute_baseline
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store


def _result(character, seed, progress=0.089):
    return TrajectoryResult(
        trajectory_id=seed, status="completed", progress=progress, ascended=False,
        steps=1, turns=1, max_depth=3, end_status="died", error=None,
        wall_seconds=0.1, character=character, milestone="Dlvl:3",
    )


def _fake_evidence(spec):
    # One result per (seed, character) in the published batch, as a real eval
    # would produce; the (self-reported) tier here is overridden to "baseline".
    results = [_result(character, seed) for seed, character in spec.batch]
    return Evidence.from_results(
        solution_digest="autoascend",
        objective=Objective(character=None, seed_set=spec.name),
        evaluator_image="img", results=results, created_at="t", tier="self-reported",
    )


def _fake_eval(tree, spec, image, *, now, runner=None, **kw):
    return 0.089, _fake_evidence(spec)


def test_compute_baseline_stores_atoms_as_autoascend(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    spec = CATALOG["val-dwa-law-fem"]
    n = compute_baseline(store, [spec], image="img", now="t", evaluate_fn=_fake_eval)
    assert n == len(spec.batch)
    ident = spec.characters()[0]
    got = store.iter_baseline_atoms(identity=ident)
    assert got
    assert got[0].owner == "autoascend"
    assert got[0].solution_digest == "autoascend"
    assert got[0].tier == "baseline"                 # forced, not the evidence's tier
    # isolation: nothing leaked into the participant atoms table
    assert store.conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0


def test_compute_baseline_is_idempotent(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    spec = CATALOG["val-dwa-law-fem"]
    compute_baseline(store, [spec], image="img", now="t", evaluate_fn=_fake_eval)
    compute_baseline(store, [spec], image="img", now="t", evaluate_fn=_fake_eval)  # re-run
    got = store.iter_baseline_atoms(identity=spec.characters()[0])
    assert len(got) == len(spec.batch)               # replaced, not doubled
