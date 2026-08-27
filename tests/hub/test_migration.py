"""Tests for the atoms/baseline_atoms migration off the legacy
``objective_digest`` column (Task A4). A3 changed the fresh-DB DDL to be
identity-keyed (``UNIQUE(solution_digest, identity, seed)``), but a live DB
still carries the old shape. ``Store.init_schema()`` must detect a legacy
table (the ``objective_digest`` column is present) and rebuild it in the new
shape -- a no-op on a fresh or already-migrated DB.

For ``atoms``, which the new shape gives a real ``UNIQUE(solution_digest,
identity, seed)`` index, this collapses any duplicate rows to the EARLIEST
(by insertion order). ``baseline_atoms`` has no such unique key even in the
new shape (``insert_baseline_atoms`` is deliberately "no dedup" -- see its
docstring), so its migration only drops the column; every row is carried
over losslessly.

``baseline_atoms`` (unlike ``atoms``) has never had an ``id`` column, even
in its legacy shape (see 940298d) -- so the migration must order the old
rows by sqlite's implicit ``rowid``, not a column named ``id``, or it
crashes on exactly the table Task A4 exists to fix (prod's real
AutoAscend-computed baseline_atoms, still in the legacy shape)."""

from __future__ import annotations

import sqlite3

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

_OLD_ATOMS_DDL = """
CREATE TABLE atoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_digest TEXT NOT NULL, objective_digest TEXT NOT NULL,
    owner TEXT NOT NULL, tier TEXT NOT NULL, identity TEXT NOT NULL,
    seed INTEGER NOT NULL, progression REAL NOT NULL, milestone TEXT,
    ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, objective_digest, seed)
);
"""

_OLD_BASELINE_ATOMS_DDL = """
CREATE TABLE baseline_atoms (
    solution_digest TEXT NOT NULL, objective_digest TEXT NOT NULL,
    owner TEXT NOT NULL, tier TEXT NOT NULL, identity TEXT NOT NULL,
    seed INTEGER NOT NULL, progression REAL NOT NULL, milestone TEXT,
    ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_COLS = ("solution_digest,objective_digest,owner,tier,identity,seed,"
         "progression,milestone,ascended,status,turns,steps,evaluator_image")
_PLACEHOLDERS = ",".join("?" * 13)


def _row(sol, obj, seed, prog):
    return (sol, obj, "sam", "self-reported", "val-dwa-law-fem", seed, prog,
            None, 0, "completed", 1, 1, "img")


def test_migration_collapses_duplicates_keeping_earliest(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_ATOMS_DDL)
    conn.execute("CREATE TABLE solutions (digest TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO solutions VALUES ('sha256:s')")
    # Two atoms, same (solution, identity, seed) but different objective_digest
    # -> collide on the NEW key; the earlier-inserted (progression 0.4) wins.
    conn.execute(f"INSERT INTO atoms ({_COLS}) VALUES ({_PLACEHOLDERS})",
                 _row("sha256:s", "obj-a", 0, 0.4))
    conn.execute(f"INSERT INTO atoms ({_COLS}) VALUES ({_PLACEHOLDERS})",
                 _row("sha256:s", "obj-b", 0, 0.9))
    conn.commit()
    conn.close()

    store = Store(db)
    store.init_schema()   # runs the migration
    atoms = store.iter_atoms(identity="val-dwa-law-fem")
    assert len(atoms) == 1
    assert atoms[0].progression == 0.4                     # earliest kept
    cols_now = [r[1] for r in store.conn.execute("PRAGMA table_info(atoms)")]
    assert "objective_digest" not in cols_now


def test_migration_is_a_noop_on_a_fresh_db(tmp_path):
    store = Store(tmp_path / "fresh.db")
    store.init_schema()
    store.init_schema()   # idempotent, no crash
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(atoms)")]
    assert "objective_digest" not in cols


def test_migration_carries_over_baseline_atoms_without_an_id_column(tmp_path):
    # baseline_atoms has NO id column, even pre-migration, and no UNIQUE
    # constraint (insert_baseline_atoms is deliberately "no dedup" -- see
    # its docstring) -- so migration must (a) not crash ordering by a
    # nonexistent "id" column and (b) carry every row over losslessly,
    # since there is no key for INSERT OR IGNORE to collapse duplicates on.
    db = tmp_path / "old_baseline.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_BASELINE_ATOMS_DDL)
    conn.execute(f"INSERT INTO baseline_atoms ({_COLS}) VALUES ({_PLACEHOLDERS})",
                 _row("autoascend", "obj-a", 3, 0.2))
    conn.execute(f"INSERT INTO baseline_atoms ({_COLS}) VALUES ({_PLACEHOLDERS})",
                 _row("autoascend", "obj-b", 3, 0.7))
    conn.commit()
    conn.close()

    store = Store(db)
    store.init_schema()   # must not crash on the id-less legacy table
    atoms = store.iter_baseline_atoms(identity="val-dwa-law-fem")
    assert {a.progression for a in atoms} == {0.2, 0.7}     # lossless: both survive
    cols_now = [r[1] for r in store.conn.execute("PRAGMA table_info(baseline_atoms)")]
    assert "objective_digest" not in cols_now


def test_migration_is_a_noop_on_an_already_migrated_db(tmp_path):
    store = Store(tmp_path / "fresh2.db")
    store.init_schema()
    store.upsert_solution(
        "sha256:s", repo="r", commit_sha="c", owner="sam", root=".",
        entrypoint="bot.py", registered_at="2026-08-27T00:00:00Z")
    store.insert_atoms([
        Atom(solution_digest="sha256:s", owner="sam", tier="self-reported",
             identity="val-dwa-law-fem", seed=0, progression=0.5, milestone=None,
             ascended=False, status="completed", turns=1, steps=1, evaluator_image="img")
    ])

    store.init_schema()   # second call must not disturb already-migrated data

    atoms = store.iter_atoms(identity="val-dwa-law-fem")
    assert len(atoms) == 1
    assert atoms[0].progression == 0.5
