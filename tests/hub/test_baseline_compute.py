"""Tests for baseline_compute: run AutoAscend (injected fake evaluate, no
Docker/arena) on an ObjectiveSpec and store the atoms in baseline_atoms, marked
owner/solution="autoascend", tier="baseline", isolated from participant atoms."""

from __future__ import annotations

import pytest

from nethackers import _image_pins
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.baseline_compute import compute_baseline
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import UnclassifiedArena, WrongArenaMajor

# The baseline writes straight into baseline_atoms, so its image is held to
# the same admission rule register enforces (spec 2026-09-14 D5): the pin.
_PIN = _image_pins.ARENA_IMAGE


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
        evaluator_image=_PIN, results=results, created_at="t", tier="self-reported",
    )


def _fake_eval(tree, spec, image, *, now, runner=None, **kw):
    return 0.089, _fake_evidence(spec)


def test_compute_baseline_stores_atoms_as_autoascend(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    spec = CATALOG["val-dwa-law-fem"]
    n = compute_baseline(store, [spec], image=_PIN, now="t", evaluate_fn=_fake_eval)
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
    compute_baseline(store, [spec], image=_PIN, now="t", evaluate_fn=_fake_eval)
    compute_baseline(store, [spec], image=_PIN, now="t", evaluate_fn=_fake_eval)  # re-run
    got = store.iter_baseline_atoms(identity=spec.characters()[0])
    assert len(got) == len(spec.batch)               # replaced, not doubled


def test_compute_baseline_refuses_an_unclassified_arena_image(tmp_path):
    """The one path that writes to baseline_atoms without going through
    register. Before this guard, a local arm64 tag could set the reference
    FLOOR every public score is read against -- measured on an arena nobody
    else is allowed to submit from. Refused up front, before hours of
    episodes: the guard is the first statement in compute_baseline."""
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    spec = CATALOG["val-dwa-law-fem"]

    with pytest.raises(UnclassifiedArena):
        compute_baseline(store, [spec], image="nethackers/arena:dev", now="t",
                         evaluate_fn=_fake_eval)

    with pytest.raises(WrongArenaMajor):
        compute_baseline(
            store, [spec], now="t", evaluate_fn=_fake_eval,
            # major 1: the pre-amd64 arena, retired by this design's bump.
            image="ghcr.io/dunnolab/nethackers-arena@sha256:"
                  "9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142",
        )

    # Nothing ran and nothing was written -- not even a partial recompute.
    assert store.conn.execute("SELECT COUNT(*) FROM baseline_atoms").fetchone()[0] == 0
