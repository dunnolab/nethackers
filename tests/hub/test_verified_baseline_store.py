"""Tests for the ``verified_baseline_atoms`` store methods: AutoAscend's
hidden-seed reference atoms live in their own table, isolated from BOTH the
participant ``atoms`` table and the participant ``verified_atoms`` table, so
the floor can never rank as a participant. Unlike ``baseline_atoms`` (public
seeds, no epoch), these rows are scoped by ``secret_fingerprint`` +
``evaluator_image`` -- rotate the secret or re-pin the arena and the old floor
stops counting instead of silently comparing across epochs."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

FP = "a" * 64
TOKFP = "b" * 64
IMG = "img@sha256:x"


def _atom(ident="val-dwa-law-fem", seed=4839201, prog=0.089, image=IMG):
    return Atom(solution_digest="autoascend", owner="autoascend", tier="baseline",
                identity=ident, seed=seed, progression=prog, milestone="Dlvl:3",
                ascended=False, status="completed", turns=1, steps=1,
                evaluator_image=image)


def _store(tmp_path):
    s = Store(str(tmp_path / "h.db"))
    s.init_schema()
    return s


def _insert(store, atoms, *, fp=FP, tokfp=TOKFP):
    return store.insert_verified_baseline_atoms(
        atoms, secret_fingerprint=fp, verifier_token_fingerprint=tokfp
    )


def test_roundtrip_and_isolation_from_participant_tables(tmp_path):
    store = _store(tmp_path)
    assert _insert(store, [_atom(), _atom(ident="wiz-elf-cha-mal", prog=0.05)]) == 2
    got = store.iter_verified_baseline_atoms(identity="val-dwa-law-fem")
    assert len(got) == 1
    assert abs(got[0].progression - 0.089) < 1e-9
    assert got[0].ascended is False  # bool, not int 0 -- mirrors iter_atoms
    # The floor is never a participant: neither participant table sees it.
    assert store.iter_atoms() == []
    assert store.iter_verified_atoms() == []


def test_insert_dedups_on_unique_key(tmp_path):
    store = _store(tmp_path)
    atoms = [_atom(seed=1), _atom(seed=2)]
    assert _insert(store, atoms) == 2
    assert _insert(store, atoms) == 0


def test_rotation_new_secret_fingerprint_does_not_collide(tmp_path):
    """A secret rotation must not overwrite the previous epoch's floor --
    history is retained, the fingerprint keeps the epochs apart."""
    store = _store(tmp_path)
    _insert(store, [_atom(seed=1)])
    _insert(store, [_atom(seed=1)], fp="c" * 64)
    assert len(store.iter_verified_baseline_atoms()) == 2
    assert len(store.iter_verified_baseline_atoms(secret_fingerprint=FP)) == 1


def test_re_pinned_arena_image_does_not_collide(tmp_path):
    """Same reasoning for the other epoch axis: a re-pinned arena image is a
    different measurement, not a replacement of the old one."""
    store = _store(tmp_path)
    _insert(store, [_atom(seed=1)])
    _insert(store, [_atom(seed=1, image="img@sha256:new")])
    assert len(store.iter_verified_baseline_atoms(evaluator_image=IMG)) == 1
    assert len(store.iter_verified_baseline_atoms()) == 2


def test_rows_are_attributable_to_the_submitting_verifier(tmp_path):
    """Two verifier boxes may both submit a floor; a row must say which one
    produced it, so a disagreement is traceable to a box."""
    store = _store(tmp_path)
    _insert(store, [_atom(seed=1)], tokfp=TOKFP)
    _insert(store, [_atom(seed=2)], tokfp="d" * 64)
    only_mac = store.iter_verified_baseline_atoms(verifier_token_fingerprint="d" * 64)
    assert [a.seed for a in only_mac] == [2]


def test_iter_rejects_unknown_filter(tmp_path):
    store = _store(tmp_path)
    try:
        store.iter_verified_baseline_atoms(bogus="x")
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown filter key")
