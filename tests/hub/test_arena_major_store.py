"""Verified rows key on arena_major; existing rows migrate into it."""

import logging
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


def _legacy_attempt_row(conn, *, image, status, failure_kind, message,
                        identities_done, at):
    conn.execute(
        "INSERT INTO verified_attempts (solution_digest, secret_fingerprint,"
        " evaluator_image, verifier_token_fingerprint, status, failure_kind,"
        " message, identities_done, at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("repo@abc", "secretfp", image, "tokenfp", status, failure_kind,
         message, identities_done, at),
    )


class _FailAfterNInserts(sqlite3.Connection):
    """Test double for one specific ``sqlite3.Connection``: raises
    ``OperationalError`` on the Nth ``INSERT OR IGNORE INTO <table>`` it
    sees after being armed, to simulate the mid-loop failure (disk full,
    ``database is locked``, a killed process) a real migration could hit
    partway through its insert loop, without needing to actually exhaust
    disk or kill anything."""

    def _armed_for(self, table: str, fail_after: int) -> None:
        self._fail_table = table
        self._fail_after = fail_after
        self._insert_count = 0

    def execute(self, sql, *args, **kwargs):
        if (
            getattr(self, "_fail_after", 0)
            and sql.startswith(f"INSERT OR IGNORE INTO {self._fail_table}")
        ):
            self._insert_count += 1
            if self._insert_count == self._fail_after:
                raise sqlite3.OperationalError("database or disk is full")
        return super().execute(sql, *args, **kwargs)


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


def test_migration_covers_all_three_legacy_tables_in_one_pass(tmp_path):
    """The loop in _migrate_add_arena_major runs over all three verified
    tables, and verified_attempts is the structurally different one: it is
    the only one with an `id` column, so it is the only one that exercises
    the `carried = [c for c in cols if c != "id"]` filter and the fresh-
    AUTOINCREMENT renumbering that follows from dropping that column from
    the carried set. Every test above seeds only verified_atoms, so none of
    them would catch a regression in either -- seed all three so this one
    does, and confirm the renumbering preserves insertion order by making
    the chronologically LATER legacy attempt the one that must still sort
    last after its id is reassigned."""
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    _legacy_atom_row(conn, "verified_baseline_atoms", seed=2, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    _legacy_attempt_row(conn, image=OLD_IMAGE, status="failed",
                        failure_kind="crashed", message="boom",
                        identities_done=0, at="2026-09-01T00:00:00Z")
    _legacy_attempt_row(conn, image=NEW_IMAGE, status="succeeded",
                        failure_kind=None, message=None,
                        identities_done=73, at="2026-09-02T00:00:00Z")
    conn.commit()
    conn.close()

    store = Store(str(path))
    store.init_schema()

    assert len(store.iter_verified_atoms(arena_major=1)) == 1
    assert len(store.iter_verified_baseline_atoms(arena_major=1)) == 1
    latest = store.latest_verified_attempt(
        "repo@abc", secret_fingerprint="secretfp", arena_major=1)
    assert latest is not None
    assert latest["status"] == "succeeded", (
        "the chronologically later legacy attempt must still sort last "
        "once verified_attempts' id column is dropped and reassigned "
        "fresh by the rename-and-copy migration"
    )


def test_migration_logs_a_warning_when_it_pools_a_duplicate(tmp_path, caplog):
    """A migration that silently drops rows -- however legitimate the
    pooling -- must leave a record somewhere a human could find it. The
    return value alone isn't enough: init_schema (the only production call
    site) previously discarded it outright."""
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=NEW_IMAGE,
                     created_at="2026-09-02T00:00:00Z")
    conn.commit()
    conn.close()

    store = Store(str(path))
    with caplog.at_level(logging.WARNING, logger="nethackers.hub.store"):
        store.init_schema()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("pooled 1" in r.getMessage() for r in warnings), (
        f"expected a WARNING naming the dropped count; got {warnings!r}"
    )


def test_migration_failure_partway_leaves_the_original_table_intact(tmp_path):
    """Direct regression test for the Critical review finding: a raise
    partway through the insert loop must not strand the original rows
    behind a renamed `_old` table while leaving a new, EMPTY, already-
    migrated-looking `verified_atoms` in their place. That requires the
    whole rename+recreate+backfill+drop to be ONE transaction -- sqlite3
    never opens one on its own for a bare DDL statement, and
    conn.executescript() (the old code's recreate step) commits early
    regardless -- so this fails without the explicit `BEGIN IMMEDIATE` +
    single-table `conn.execute` in _migrate_add_arena_major."""
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path, factory=_FailAfterNInserts)
    conn.executescript(LEGACY_SCHEMA)
    for seed in (1, 2, 3):
        _legacy_atom_row(conn, "verified_atoms", seed=seed, image=OLD_IMAGE,
                         created_at="2026-09-01T00:00:00Z")
    conn.commit()

    conn._armed_for("verified_atoms", fail_after=2)
    with pytest.raises(sqlite3.OperationalError):
        _migrate_add_arena_major(conn)

    # The original table, under its original name, with every row intact --
    # not emptied, not renamed, no `_old` sibling stranded behind it.
    assert [r[0] for r in conn.execute(
        "SELECT seed FROM verified_atoms ORDER BY seed")] == [1, 2, 3]
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND"
        " name = 'verified_atoms_old'"
    ).fetchone() is None
    conn.close()

    # Genuinely retryable through the real production entrypoint -- nothing
    # was left half-migrated for the shape guard to misread as finished.
    store = Store(str(path))
    store.init_schema()
    assert len(store.iter_verified_atoms(arena_major=1)) == 3


def test_migration_refuses_when_an_old_table_is_already_stranded(tmp_path):
    """Defense in depth alongside the atomicity fix above: if a `_old`
    table is ever found already sitting next to a live one -- a scar from
    some migration attempt that did not finish cleanly, by whatever means
    -- this must refuse outright rather than trust the ordinary shape
    guard, which cannot tell a genuinely fresh table apart from one that
    was prematurely recreated while its predecessor's data is still
    sitting, unmigrated, in `_old`."""
    path = tmp_path / "hub.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    _legacy_atom_row(conn, "verified_atoms", seed=1, image=OLD_IMAGE,
                     created_at="2026-09-01T00:00:00Z")
    # Stands in for the scar an incomplete migration attempt would leave.
    conn.execute("CREATE TABLE verified_atoms_old (x INTEGER)")
    conn.commit()

    with pytest.raises(RuntimeError, match="verified_atoms_old"):
        _migrate_add_arena_major(conn)
    assert conn.execute("SELECT COUNT(*) FROM verified_atoms").fetchone()[0] == 1
    conn.close()
