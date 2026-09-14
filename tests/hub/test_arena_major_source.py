"""Verified reads are scoped by arena major, so one major pools two digests."""

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.source import Epoch, source_for

OLD_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142"
)
NEW_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3"
)


def _atom(seed: int, image: str) -> Atom:
    return Atom(
        solution_digest="repo@abc", owner="someone", tier="verified",
        identity="val-dwa-law-fem", seed=seed, progression=0.5,
        milestone="Dlvl:3", ascended=False, status="completed", turns=100, steps=200,
        evaluator_image=image,
    )


def test_one_major_pools_rows_from_two_digests(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    store.insert_verified_atoms(
        [_atom(1, OLD_IMAGE), _atom(2, NEW_IMAGE)],
        secret_fingerprint="secretfp", verifier_token_fingerprint="tokenfp",
        arena_major=1)

    epoch = Epoch(secret_fingerprint="secretfp", arena_major=1, seeds=(1, 2))
    rows = source_for("verified", epoch).iter_atoms(store)

    assert len(rows) == 2
    assert {r.evaluator_image for r in rows} == {OLD_IMAGE, NEW_IMAGE}


def test_a_different_major_reads_nothing(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    store.insert_verified_atoms(
        [_atom(1, OLD_IMAGE)], secret_fingerprint="secretfp",
        verifier_token_fingerprint="tokenfp", arena_major=1)

    epoch = Epoch(secret_fingerprint="secretfp", arena_major=2, seeds=(1, 2))
    assert source_for("verified", epoch).iter_atoms(store) == []


def test_where_clause_filters_on_the_major(tmp_path):
    epoch = Epoch(secret_fingerprint="secretfp", arena_major=1, seeds=(1, 2))
    sql, params = source_for("verified", epoch).where()
    assert "arena_major = ?" in sql
    assert "evaluator_image" not in sql
    assert params == ("secretfp", 1, 1, 2)
