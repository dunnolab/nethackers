"""Tests for ``register_verified_baseline`` -- the hidden-seed AutoAscend
floor's write path. It is ``register_verified``'s sibling minus the solution
lookup: AutoAscend has no ``solutions`` row (and must never get one, or it
would rank as a participant), so ownership is a constant rather than something
resolved from the hub. What it still enforces is the trust ladder specific to
hidden-seed evidence: parity, secret epoch, batch membership, finite metrics."""

from __future__ import annotations

from dataclasses import replace

import pytest

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.verify import (
    BadBatch,
    NonFiniteMetrics,
    ParityMismatch,
    StaleSecret,
    register_verified_baseline,
)

SECRET = "hidden-key"
SEEDS = (4839201, 1029384)
TOKFP = "b" * 64


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    return s


def _evidence(identity="val-dwa-law-fem", seeds=SEEDS, image=ARENA_IMAGE, progress=0.4):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=progress,
                         ascended=False, steps=1, turns=1, max_depth=1, end_status="died",
                         error=None, wall_seconds=0.1, character=identity, milestone=None)
        for sd in seeds)
    return Evidence.from_results(
        solution_digest="autoascend", objective=Objective(character=None, seed_set="v"),
        evaluator_image=image, results=results, created_at="t")


def _register(store, evidence, *, fp=None, image=ARENA_IMAGE, seeds=SEEDS):
    return register_verified_baseline(
        store, evidence=evidence,
        secret_fingerprint=fp if fp is not None else secret_fingerprint(SECRET),
        verifier_token_fingerprint=TOKFP, expected_image=image,
        hub_secret=SECRET, seeds=seeds)


def test_writes_the_floor_as_autoascend_and_never_as_a_participant(tmp_path):
    s = _store(tmp_path)
    res = _register(s, _evidence())
    assert res.inserted == 2
    assert res.total == len(IDENTITIES) * len(SEEDS)
    assert res.done == len(SEEDS)  # one identity submitted, every seed of it covered
    atoms = s.iter_verified_baseline_atoms()
    assert len(atoms) == 2
    assert all(a.owner == "autoascend" and a.tier == "baseline" for a in atoms)
    # Isolation: neither participant table is touched.
    assert s.iter_atoms() == []
    assert s.iter_verified_atoms() == []


def test_caller_supplied_tier_is_never_trusted(tmp_path):
    """Evidence arrives over HTTP from a verifier box, and ``evidence.tier``
    is caller-controlled. Even when it claims to be a participant submission,
    the stored floor is forced to ``baseline`` -- the same defence
    ``register_verified`` applies in the other direction."""
    s = _store(tmp_path)
    _register(s, replace(_evidence(), tier="self-reported"))
    stored = s.iter_verified_baseline_atoms()
    assert {a.tier for a in stored} == {"baseline"}
    assert {a.owner for a in stored} == {"autoascend"}


def test_resubmitting_the_same_batch_is_idempotent(tmp_path):
    s = _store(tmp_path)
    assert _register(s, _evidence()).inserted == 2
    again = _register(s, _evidence())
    assert again.inserted == 0
    assert again.done == len(SEEDS)  # coverage still reports the true total
    assert len(s.iter_verified_baseline_atoms()) == 2


def test_rejects_evidence_from_an_unpinned_arena_image(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ParityMismatch):
        _register(s, _evidence(image="somebody/arena:local"))
    assert s.iter_verified_baseline_atoms() == []


def test_rejects_a_stale_secret_epoch(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(StaleSecret):
        _register(s, _evidence(), fp=secret_fingerprint("an-older-secret"))
    assert s.iter_verified_baseline_atoms() == []


def test_rejects_seeds_outside_the_hidden_batch(tmp_path):
    """The floor must be measured on exactly the seeds participants are, or
    the comparison is dishonest."""
    s = _store(tmp_path)
    with pytest.raises(BadBatch):
        _register(s, _evidence(seeds=(1, 2)))
    assert s.iter_verified_baseline_atoms() == []


def test_rejects_an_unknown_identity(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(BadBatch):
        _register(s, _evidence(identity="not-an-identity"))
    assert s.iter_verified_baseline_atoms() == []


def test_rejects_non_finite_progress(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(NonFiniteMetrics):
        _register(s, _evidence(progress=float("inf")))
    assert s.iter_verified_baseline_atoms() == []
