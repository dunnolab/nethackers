"""Tests for ``read_verified_baseline`` -- the hidden-seed AutoAscend floor
read. Same ``{per_identity, overall}`` shape as ``read_verified`` so the
Verified board can put a program's number next to the floor's without
reshaping either, and scoped to the same epoch (secret fingerprint + arena
major + current seed list) so a Delta is never computed across epochs."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.verified import read_verified_baseline

FP = "a" * 64
TOKFP = "b" * 64
IMG = "img@sha256:x"
SEEDS = (4839201, 1029384)


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    return s


def _atom(seed, identity="val-dwa-law-fem", prog=0.4, image=IMG, milestone="Dlvl:3"):
    return Atom(solution_digest="autoascend", owner="autoascend", tier="baseline",
                identity=identity, seed=seed, progression=prog, milestone=milestone,
                ascended=False, status="completed", turns=1, steps=1, evaluator_image=image)


def _insert(store, atoms, *, arena_major, fp=FP):
    store.insert_verified_baseline_atoms(
        atoms, secret_fingerprint=fp, verifier_token_fingerprint=TOKFP,
        arena_major=arena_major,
    )


def _read(store):
    return read_verified_baseline(
        store, secret_fingerprint=FP, seeds=SEEDS, arena_major=1
    )


def test_aggregates_progression_and_deepest_milestone_per_identity(tmp_path):
    s = _store(tmp_path)
    _insert(s, [_atom(4839201, prog=0.4, milestone="Dlvl:3"),
                _atom(1029384, prog=0.6, milestone="Dlvl:5")], arena_major=1)
    got = _read(s)
    assert got["per_identity"]["val-dwa-law-fem"] == {
        "progression": 0.5, "deepest": "Dlvl:5", "episodes": 2,
    }
    assert got["overall"] == 0.5


def test_overall_is_the_mean_across_identities(tmp_path):
    s = _store(tmp_path)
    _insert(s, [_atom(4839201, identity="val-dwa-law-fem", prog=0.4),
                _atom(4839201, identity="wiz-elf-cha-mal", prog=0.2)], arena_major=1)
    assert _read(s)["overall"] == 0.3


def test_excludes_other_epochs(tmp_path):
    """A floor measured under a rotated secret, a bumped arena major, or a
    seed that is no longer in the hidden list must not leak into today's
    number -- each would make Delta-vs-AA a comparison across different
    measurements. A re-pinned image alone (same major) must NOT exclude --
    that pooling case is covered in test_arena_major_source.py."""
    s = _store(tmp_path)
    _insert(s, [_atom(4839201, prog=0.4)], arena_major=1)
    _insert(s, [_atom(1029384, prog=1.0)], arena_major=1, fp="c" * 64)  # rotated secret
    _insert(s, [_atom(1029384, prog=1.0, image="img@sha256:old")],
            arena_major=2)                                          # bumped major
    _insert(s, [_atom(999, prog=1.0)], arena_major=1)                  # retired seed
    got = _read(s)
    assert got["per_identity"]["val-dwa-law-fem"]["episodes"] == 1
    assert got["overall"] == 0.4


def test_empty_floor_reads_as_none_not_zero(tmp_path):
    """No floor computed yet must be distinguishable from a floor of 0.0 --
    otherwise the board would show every program beating AutoAscend."""
    s = _store(tmp_path)
    got = _read(s)
    assert got["per_identity"] == {}
    assert got["overall"] is None
