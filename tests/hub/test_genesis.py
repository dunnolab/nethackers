"""Tests for ``Store.genesis``: the one-shot archival of the live PUBLIC
tables and their recreation as empty (design 2026-09-14, Sec 5.5 / D3 / I8).

The risk this file exists to pin down is not "does it move rows" -- it is that
it runs ONCE, unattended, against a live production database holding
irreplaceable evaluation data. So the properties under test are the ones a
second attempt cannot repair: it archives rather than deletes, it never fires
twice, any failure leaves every table exactly as found, and the tables it hands
back are usable by the very next registration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

import pytest

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom
from nethackers.hub import genesis as genesis_cmd, store as store_mod
from nethackers.hub.ids import program_id
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment

ARCHIVED = (
    "solutions",
    "atoms",
    "baseline_atoms",
    "lineage",
    "attainment",
    "attainment_holders",
)

# The four tables genesis must leave completely alone: the verified three
# (invariant I4 retires them via the ARENA_MAJOR bump, with no migration) and
# the prophecy poll, which has nothing to do with the arena.
UNTOUCHED = (
    "verified_atoms",
    "verified_baseline_atoms",
    "verified_attempts",
    "poll_votes",
)

DIGEST = "sha256:solution-a"
PARENT_DIGEST = "sha256:solution-parent"
IDENTITY = "val-dwa-law-fem"


_BASE_ATOM = Atom(
    solution_digest=DIGEST,
    owner="sam",
    tier="self-reported",
    identity=IDENTITY,
    seed=0,
    progression=ACHIEVEMENTS["Dlvl:3"],
    milestone="Dlvl:3",
    ascended=False,
    status="completed",
    turns=5,
    steps=10,
    evaluator_image="img@sha256:x",
)


def _atom(**overrides: Any) -> Atom:
    return replace(_BASE_ATOM, **overrides)


def _register(store: Store, digest: str) -> None:
    store.upsert_solution(
        digest,
        repo="git@example.com:sam/bot.git",
        commit_sha="deadbeef",
        owner="sam",
        root="bots/sam",
        entrypoint="bot:main",
        registered_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def populated_store(tmp_path) -> Store:
    """A Store with at least one row in every table genesis touches, plus the
    four it must NOT touch. Populated through this package's own writers, never
    hand-written INSERTs, so the fixture cannot encode a table shape the module
    has since changed."""
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()

    _register(store, DIGEST)
    _register(store, PARENT_DIGEST)
    store.insert_atoms([_atom(), _atom(seed=1)])
    store.insert_baseline_atoms([_atom(owner="autoascend", tier="baseline")])
    store.add_lineage(DIGEST, PARENT_DIGEST, "parent")
    # attainment + attainment_holders (nothing in Store writes these; the view
    # owns that logic).
    update_attainment(store, [_atom()], now="2026-01-01T00:00:00Z")

    verified = _atom(tier="verified")
    store.insert_verified_atoms(
        [verified], secret_fingerprint="fp", verifier_token_fingerprint="tok",
        arena_major=1,
    )
    store.insert_verified_baseline_atoms(
        [_atom(owner="autoascend", tier="baseline")], secret_fingerprint="fp",
        verifier_token_fingerprint="tok", arena_major=1,
    )
    store.insert_verified_attempt(
        solution_digest=DIGEST, secret_fingerprint="fp",
        evaluator_image="img@sha256:x", arena_major=1,
        verifier_token_fingerprint="tok", status="completed", failure_kind=None,
        message=None, identities_done=1, at="2026-01-01T00:00:00Z",
    )
    store.upsert_poll_vote(
        "voter-1", method="search", timeline="2027", roles=["val"], xp="low")
    return store


def _row_count(store: Store, table: str) -> int:
    return store._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _table_exists(store: Store, table: str) -> bool:
    row = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _table_sql(store: Store, table: str) -> str:
    return store._conn.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]


def _index_table(store: Store, index: str) -> str | None:
    row = store._conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (index,)
    ).fetchone()
    return row[0] if row else None


def test_genesis_archives_every_public_table_and_leaves_them_empty(populated_store):
    counts = populated_store.genesis()
    for table in ARCHIVED:
        assert _table_exists(populated_store, f"{table}_v1")
        assert _row_count(populated_store, table) == 0
    assert counts["atoms"] > 0


def test_genesis_is_idempotent(populated_store):
    first = populated_store.genesis()
    second = populated_store.genesis()
    assert first != {}
    assert second == {}


def test_genesis_does_not_touch_the_untouchable_tables(populated_store):
    before = {t: _row_count(populated_store, t) for t in UNTOUCHED}
    populated_store.genesis()
    for table in UNTOUCHED:
        assert _row_count(populated_store, table) == before[table]
        assert not _table_exists(populated_store, f"{table}_v1")


def test_genesis_archives_rather_than_deletes(populated_store):
    counts = populated_store.genesis()
    for table, rows in counts.items():
        assert _row_count(populated_store, f"{table}_v1") == rows
    assert counts["atoms"] == 2


def test_a_second_genesis_cannot_archive_rows_registered_after_the_first(
        populated_store):
    # The guard is on the ARCHIVE's presence, not on ARENA_MAJOR, precisely so
    # a rollback-and-redeploy cannot empty a board that has since refilled.
    populated_store.genesis()
    _register(populated_store, "sha256:after")
    populated_store.insert_atoms([_atom(solution_digest="sha256:after")])

    assert populated_store.genesis() == {}
    assert _row_count(populated_store, "atoms") == 1
    assert _row_count(populated_store, "atoms_v1") == 2


def test_a_failure_partway_through_leaves_every_table_as_found(
        populated_store, monkeypatch):
    # One transaction (I8): inject a table that does not exist at the END of
    # the map, so the loop renames and recreates every real table first and
    # only then raises. Everything must roll back -- a half-applied genesis
    # strands the live rows behind a table that already looks migrated.
    broken = {**store_mod._PUBLIC_TABLE_DDL, "no_such_table": "CREATE TABLE x (a)"}
    monkeypatch.setattr(store_mod, "_PUBLIC_TABLE_DDL", broken)

    with pytest.raises(sqlite3.OperationalError):
        populated_store.genesis()

    for table in ARCHIVED:
        assert not _table_exists(populated_store, f"{table}_v1")
    assert _row_count(populated_store, "atoms") == 2
    assert _row_count(populated_store, "solutions") == 2
    assert len(populated_store.iter_atoms(identity=IDENTITY)) == 2
    # And the rolled-back attempt left nothing that blocks a real one.
    monkeypatch.undo()
    assert populated_store.genesis()["atoms"] == 2


def test_the_fresh_tables_bind_their_foreign_keys_to_the_fresh_solutions(
        populated_store):
    # ALTER TABLE ... RENAME TO rewrites OTHER tables' REFERENCES clauses while
    # PRAGMA foreign_keys is ON. Renaming solutions first is what keeps the
    # recreated children pointing at the live table instead of the archive; get
    # the order wrong and every later insert_atoms FK-checks the archive.
    populated_store.genesis()
    assert 'REFERENCES "solutions_v1"' in _table_sql(populated_store, "atoms_v1")
    for table in ("atoms", "lineage"):
        assert "solutions_v1" not in _table_sql(populated_store, table)

    _register(populated_store, "sha256:after")
    assert populated_store.insert_atoms([_atom(solution_digest="sha256:after")]) == 1
    populated_store.add_lineage("sha256:after", DIGEST, "parent")


def test_registration_still_works_immediately_after_genesis(populated_store):
    # _SCHEMA's solutions DDL is not the whole live shape: program_id and its
    # unique index are added additively by _migrate_add_program_id. Recreating
    # from the DDL alone yields a table upsert_solution cannot insert into, so
    # registration would break for everyone the moment genesis committed.
    populated_store.genesis()
    _register(populated_store, "sha256:after")

    assert populated_store.get_solution("sha256:after") is not None
    assert populated_store.digest_for_program_id(
        program_id("sha256:after")) == "sha256:after"
    assert _index_table(populated_store, "idx_solutions_program_id") == "solutions"
    assert _index_table(
        populated_store, "idx_solutions_v1_program_id") == "solutions_v1"


def test_the_next_hub_boot_is_a_clean_no_op(populated_store):
    # Every uvicorn worker calls init_schema() at boot. After genesis that must
    # neither refill nor re-empty anything, and must not move the program_id
    # index back onto the archive.
    populated_store.genesis()
    _register(populated_store, "sha256:after")
    populated_store.init_schema()

    assert _row_count(populated_store, "solutions") == 1
    assert _row_count(populated_store, "atoms") == 0
    assert _row_count(populated_store, "solutions_v1") == 2
    assert _index_table(populated_store, "idx_solutions_program_id") == "solutions"
    assert populated_store.digest_for_program_id(
        program_id("sha256:after")) == "sha256:after"


def test_the_operator_command_reports_counts_then_says_it_is_done(
        populated_store, capsys):
    # The whole operator-facing surface: one run archives and says what it
    # moved; a second says so rather than doing anything.
    db = populated_store._db_path

    assert genesis_cmd.main(["--db", db]) == 0
    assert "atoms -> atoms_v1" in capsys.readouterr().out

    assert genesis_cmd.main(["--db", db]) == 0
    assert "already applied" in capsys.readouterr().out
    assert _row_count(populated_store, "atoms_v1") == 2


def test_the_operator_command_refuses_a_database_that_is_not_there(tmp_path, capsys):
    # A typo'd --db must not silently create an empty database and report a
    # successful all-zero genesis over it. The check runs BEFORE Store() --
    # opening a sqlite connection creates the file -- so the mistyped path is
    # left untouched, and the operator gets a sentence and a non-zero exit
    # code rather than a raw sqlite traceback.
    typo = tmp_path / "typo.db"

    assert genesis_cmd.main(["--db", str(typo)]) == 2

    assert not typo.exists()
    err = capsys.readouterr().err
    assert "no database at" in err
    assert "Traceback" not in err


def test_the_operator_command_refuses_a_database_that_is_not_a_hub(tmp_path, capsys):
    # A file that DOES exist but holds no hub schema is a different mistake,
    # and still one the command must not paper over. This used to assert a raw
    # sqlite3.OperationalError, which prevented damage but was a crash, not a
    # refusal -- on a destructive command run by hand against production. The
    # case is real: the deployed hub's data directory holds a zero-byte
    # /data/hub.db beside the real /data/hub.sqlite3, and it passes is_file().
    # Found by rehearsing genesis against a production snapshot.
    not_a_hub = tmp_path / "hub.db"
    sqlite3.connect(not_a_hub).close()
    assert genesis_cmd.main(["--db", str(not_a_hub)]) == 2
    err = capsys.readouterr().err
    assert "is not a hub database" in err
    assert "hub.sqlite3" in err
    assert "Traceback" not in err


def test_public_ddl_map_stays_a_view_onto_the_schema():
    # The map must remain a *view* onto _SCHEMA, never a second copy: genesis
    # recreates from the map while init_schema creates from _SCHEMA, and a
    # drift between them hands the live hub a differently shaped table.
    for table, ddl in store_mod._PUBLIC_TABLE_DDL.items():
        assert ddl in store_mod._SCHEMA, table
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in ddl
    assert store_mod._POLL_VOTES_DDL not in store_mod._PUBLIC_TABLE_DDL.values()
    # Insertion order is load-bearing -- see _PUBLIC_TABLE_DDL's comment.
    assert next(iter(store_mod._PUBLIC_TABLE_DDL)) == "solutions"


def test_genesis_missing_path_names_the_real_container_path(tmp_path, capsys):
    """The message used to say /data/hub.db, which is the stray empty file,
    not the database the hub actually opens (NETHACKERS_DB=/data/hub.sqlite3)."""
    from nethackers.hub.genesis import main

    assert main(["--db", str(tmp_path / "absent.db")]) == 2
    err = capsys.readouterr().err
    assert "/data/hub.sqlite3" in err
    assert "Traceback" not in err
