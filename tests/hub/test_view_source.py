"""views/source.py: the (tier, epoch) -> row-source resolver that makes the
epoch invariant structural. A verified Source hands out verified_atoms AND
verified_baseline_atoms together, so pairing a hidden-seed score with the
published-seed floor takes deliberate effort rather than being the natural
path."""

import pytest

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.source import (
    Epoch,
    VerificationUnavailable,
    source_for,
)

IMAGE = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64
OTHER_IMAGE = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "b" * 64
EPOCH = Epoch(secret_fingerprint="fp-current", evaluator_image=IMAGE, seeds=(11, 22))


def _atom(**over):
    base = dict(
        solution_digest="sha256:s", owner="sam", tier="verified",
        identity="val-dwa-law-fem", seed=11, progression=0.5, milestone="Dlvl:5",
        ascended=False, status="completed", turns=10, steps=20,
        evaluator_image=IMAGE,
    )
    base.update(over)
    return Atom(**base)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    return s


def test_self_reported_source_reads_the_atoms_table():
    src = source_for("self-reported", EPOCH)
    assert src.atoms_table == "atoms"
    assert src.epoch is None, "a self-reported source must not carry an epoch"
    assert src.where() == ("tier = ?", ("self-reported",))


def test_verified_source_reads_the_verified_table_and_filters_the_epoch():
    src = source_for("verified", EPOCH)
    assert src.atoms_table == "verified_atoms"
    sql, params = src.where()
    assert sql == (
        "secret_fingerprint = ? AND evaluator_image = ? AND seed IN (?, ?)"
    )
    assert params == ("fp-current", IMAGE, 11, 22)


def test_where_prefixes_every_column_with_the_alias():
    sql, _ = source_for("verified", EPOCH).where("a")
    assert sql == (
        "a.secret_fingerprint = ? AND a.evaluator_image = ? AND a.seed IN (?, ?)"
    )
    assert source_for("self-reported", None).where("a")[0] == "a.tier = ?"


def test_verified_without_a_configured_epoch_raises():
    with pytest.raises(VerificationUnavailable):
        source_for("verified", None)


def test_an_empty_seed_list_yields_a_never_true_predicate():
    # "seed IN ()" is a sqlite syntax error; an epoch with no seeds must match
    # nothing rather than blow up mid-request.
    src = source_for("verified", Epoch("fp", IMAGE, ()))
    assert src.where() == ("1 = 0", ())


def test_unknown_tier_falls_back_to_atoms_unchanged():
    # Preserves today's behaviour: ?tier=bogus returns no rows, never an error.
    src = source_for("bogus", EPOCH)
    assert src.atoms_table == "atoms"
    assert src.where() == ("tier = ?", ("bogus",))


def test_verified_iter_atoms_drops_wrong_fingerprint_image_and_retired_seed(store):
    store.insert_verified_atoms(
        [_atom(seed=11), _atom(seed=99)],
        secret_fingerprint="fp-current", verifier_token_fingerprint="tok",
        arena_major=1,
    )
    store.insert_verified_atoms(
        [_atom(seed=22)],
        secret_fingerprint="fp-rotated", verifier_token_fingerprint="tok",
        arena_major=1,
    )
    store.insert_verified_atoms(
        [_atom(seed=22, evaluator_image=OTHER_IMAGE)],
        secret_fingerprint="fp-current", verifier_token_fingerprint="tok",
        arena_major=1,
    )
    got = source_for("verified", EPOCH).iter_atoms(store)
    assert [a.seed for a in got] == [11], (
        "seed 99 is retired, fp-rotated is a different epoch, OTHER_IMAGE is a "
        "re-pinned arena -- all three must drop out"
    )


def test_verified_source_reads_the_verified_baseline_table(store):
    store.insert_verified_baseline_atoms(
        [_atom(owner="autoascend", tier="baseline", seed=11)],
        secret_fingerprint="fp-current", verifier_token_fingerprint="tok",
        arena_major=1,
    )
    store.insert_baseline_atoms([_atom(owner="autoascend", tier="baseline", seed=11)])

    verified = source_for("verified", EPOCH).iter_baseline_atoms(store)
    assert len(verified) == 1 and verified[0].owner == "autoascend"

    public = source_for("self-reported", None).iter_baseline_atoms(store)
    assert len(public) == 1
    # Same row count, different tables -- the pairing is what the invariant buys.


def test_self_reported_iter_atoms_does_not_leak_other_tiers(store):
    atoms = [_atom(tier="self-reported", seed=1)]
    for digest in {atom.solution_digest for atom in atoms}:
        store.upsert_solution(
            digest,
            repo="r",
            commit_sha="c",
            owner="sam",
            root=".",
            entrypoint="bot.py",
            registered_at="2026-01-01T00:00:00Z",
        )
    store.insert_atoms(atoms)
    assert len(source_for("self-reported", None).iter_atoms(store)) == 1
    assert source_for("verified", EPOCH).iter_atoms(store) == []
