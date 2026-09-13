"""Verified rows key on arena_major; existing rows migrate into it."""

import sqlite3

import pytest

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store, _migrate_add_arena_major

OLD_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142"
)
NEW_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3"
)
UNKNOWN_IMAGE = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "0" * 64

# The pre-migration DDL, copied verbatim from what shipped in v0.23.5 -- the
# shape a production database is actually in when the migration first runs.
LEGACY_SCHEMA = """
CREATE TABLE verified_atoms (
    solution_digest TEXT NOT NULL, owner TEXT NOT NULL, tier TEXT NOT NULL,
    identity TEXT NOT NULL, seed INTEGER NOT NULL, progression REAL NOT NULL,
    milestone TEXT, ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL, verifier_token_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, identity, seed, secret_fingerprint, evaluator_image)
);
CREATE TABLE verified_baseline_atoms (
    solution_digest TEXT NOT NULL, owner TEXT NOT NULL, tier TEXT NOT NULL,
    identity TEXT NOT NULL, seed INTEGER NOT NULL, progression REAL NOT NULL,
    milestone TEXT, ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL, verifier_token_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(identity, seed, secret_fingerprint, evaluator_image)
);
CREATE TABLE verified_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, solution_digest TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL, evaluator_image TEXT NOT NULL,
    verifier_token_fingerprint TEXT NOT NULL, status TEXT NOT NULL,
    failure_kind TEXT, message TEXT, identities_done INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _legacy_atom_row(conn, table, *, seed, image, created_at, progression=0.5):
    conn.execute(
        f"INSERT INTO {table} (solution_digest, owner, tier, identity, seed,"
        " progression, milestone, ascended, status, turns, steps,"
        " evaluator_image, secret_fingerprint, verifier_token_fingerprint,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("repo@abc", "someone", "verified", "val-dwa-law-fem", seed, progression,
         "Dlvl:3", 0, "ok", 100, 200, image, "secretfp", "tokenfp", created_at),
    )


def _atom(seed: int, progression: float = 0.5) -> Atom:
    return Atom(
        solution_digest="repo@abc", owner="someone", tier="verified",
        identity="val-dwa-law-fem", seed=seed, progression=progression,
        milestone="Dlvl:3", ascended=False, status="completed", turns=100, steps=200,
        evaluator_image=NEW_IMAGE,
    )


def test_migration_backfills_the_major_from_the_digest(tmp_path):
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    conn.commit()
    conn.close()

    store = Store(str(path))
    store.init_schema()

    rows = store.iter_verified_atoms(arena_major=1)
    assert len(rows) == 1
    assert rows[0].evaluator_image == OLD_IMAGE   # provenance survives (I1)


def test_migration_pools_two_digests_and_drops_the_later_duplicate(tmp_path):
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    # Same (solution, identity, seed, secret) under both digests. Distinct
    # under the old key; one row under the new one.
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z", progression=0.4)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=NEW_IMAGE,
                     created_at="2026-09-02T00:00:00Z", progression=0.9)
    conn.commit()
    dropped = _migrate_add_arena_major(conn)
    conn.close()

    assert dropped == 1
    store = Store(str(path))
    store.init_schema()
    rows = store.iter_verified_atoms(arena_major=1)
    assert len(rows) == 1
    assert rows[0].progression == 0.4        # the earliest row wins
    assert rows[0].evaluator_image == OLD_IMAGE


def test_migration_refuses_an_unclassified_digest(tmp_path):
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=UNKNOWN_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    conn.commit()
    with pytest.raises(ValueError, match="unclassified"):
        _migrate_add_arena_major(conn)
    conn.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    conn.commit()
    conn.close()

    store = Store(str(path))
    store.init_schema()
    store.init_schema()   # second run must be a no-op, not a crash
    assert len(store.iter_verified_atoms(arena_major=1)) == 1


def test_insert_and_filter_verified_atoms_by_major(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    store.insert_verified_atoms(
        [_atom(1)], secret_fingerprint="secretfp",
        verifier_token_fingerprint="tokenfp", arena_major=1,
    )
    assert len(store.iter_verified_atoms(arena_major=1)) == 1
    assert store.iter_verified_atoms(arena_major=2) == []


def test_reinserting_the_same_cell_under_a_second_digest_is_a_no_op(tmp_path):
    """Two digests, one major: the second submission dedups instead of
    doubling the cell. This is the behaviour pooling buys."""
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    first = _atom(1)
    second = Atom(**{**first.to_dict(), "evaluator_image": OLD_IMAGE,
                     "ascended": False})
    store.insert_verified_atoms(
        [first], secret_fingerprint="secretfp",
        verifier_token_fingerprint="tokenfp", arena_major=1)
    inserted = store.insert_verified_atoms(
        [second], secret_fingerprint="secretfp",
        verifier_token_fingerprint="tokenfp", arena_major=1)
    assert inserted == 0
    assert len(store.iter_verified_atoms(arena_major=1)) == 1


def test_attempts_are_looked_up_by_major_not_digest(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    store.insert_verified_attempt(
        solution_digest="repo@abc", secret_fingerprint="secretfp",
        evaluator_image=OLD_IMAGE, arena_major=1,
        verifier_token_fingerprint="tokenfp", status="failed",
        failure_kind="crashed", message="boom", identities_done=3,
        at="2026-09-01T00:00:00Z")
    # Submitted under the OTHER digest of the same major -- still found.
    found = store.latest_verified_attempt(
        "repo@abc", secret_fingerprint="secretfp", arena_major=1)
    assert found is not None
    assert found["failure_kind"] == "crashed"
    assert found["evaluator_image"] == OLD_IMAGE
    assert store.latest_verified_attempt(
        "repo@abc", secret_fingerprint="secretfp", arena_major=2) is None
