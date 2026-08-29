"""Tests for ``nethackers.hub.store``: the sqlite data layer over
solutions/objectives/atoms/lineage (+ the derived-view tables Tasks 7-8
populate). See task-5-context.md for the schema; Task A3 dropped
``objective_digest`` from atoms end-to-end, so the dedup key is now
UNIQUE(solution_digest, identity, seed) -- ``seed`` already served as the
trajectory id within a published batch, and after random/all's retirement
(Task A1) ``identity`` alone identifies which canonical objective an atom
belongs to.
"""

from __future__ import annotations

import sqlite3

import pytest

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

SOLUTION_DIGEST = "sha256:solution-a"


def test_init_schema_drops_legacy_objectives_table(tmp_path):
    # The vestigial write-only `objectives` table is dropped on init (nothing
    # reads it post identity-keying). A legacy DB that still has it must shed it.
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE objectives (objective_digest TEXT PRIMARY KEY, name TEXT UNIQUE)")
    con.execute("INSERT INTO objectives VALUES ('sha256:x', 'val-dwa-law-fem')")
    con.commit()
    con.close()

    store = Store(str(db))
    store.init_schema()
    tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "objectives" not in tables


def _atom(**overrides):
    fields = dict(
        solution_digest=SOLUTION_DIGEST,
        owner="sam",
        tier="self-reported",
        identity="val-dwa-law-fem",
        seed=0,
        progression=0.3,
        milestone="Dlvl:3",
        ascended=False,
        status="completed",
        turns=5,
        steps=10,
        evaluator_image="img@sha256:x",
    )
    fields.update(overrides)
    return Atom(**fields)


def _new_store(tmp_path):
    db_path = tmp_path / "hub.sqlite3"
    store = Store(db_path)
    store.init_schema()
    return store, db_path


def _seed_solution(store, digest=SOLUTION_DIGEST):
    store.upsert_solution(
        digest,
        repo="git@example.com:sam/bot.git",
        commit_sha="deadbeef",
        owner="sam",
        root="bots/sam",
        entrypoint="bot:main",
        registered_at="2026-08-09T00:00:00Z",
    )


def test_insert_atoms_dedups_and_returns_newly_inserted_count(tmp_path):
    # Property 1: init_schema -> insert_atoms twice with the same atoms ->
    # second call's count is 0 (idempotent dedup on the atom natural key).
    store, _ = _new_store(tmp_path)
    _seed_solution(store)
    atoms = [_atom(seed=0), _atom(seed=1)]

    first = store.insert_atoms(atoms)
    second = store.insert_atoms(atoms)

    assert first == 2
    assert second == 0


def test_atoms_dedup_on_solution_identity_seed(tmp_path):
    # The new (Task A3) dedup key: same (solution, identity, seed) collides
    # regardless of progression -- objective_digest is no longer part of it.
    store, _ = _new_store(tmp_path)
    _seed_solution(store)
    a = _atom(seed=0, identity="val-dwa-law-fem", progression=0.3)
    b = _atom(seed=0, identity="val-dwa-law-fem", progression=0.9)  # same (sol,identity,seed)
    assert store.insert_atoms([a]) == 1
    assert store.insert_atoms([b]) == 0   # dedup: identity-keyed unique
    assert len(store.iter_atoms(identity="val-dwa-law-fem")) == 1


def test_iter_atoms_filters_narrow_and_reconstruct_atom_instances(tmp_path):
    # Property 2: iter_atoms(identity=...) filters; a second filter narrows
    # further; results are real Atom instances with a real bool ascended.
    store, _ = _new_store(tmp_path)
    _seed_solution(store)
    store.insert_atoms(
        [
            _atom(seed=0, identity="val-dwa-law-fem"),
            _atom(seed=1, identity="val-dwa-law-fem"),
            _atom(seed=0, identity="wiz-elf-cha-fem"),
        ]
    )

    by_identity = store.iter_atoms(identity="val-dwa-law-fem")
    assert len(by_identity) == 2
    assert all(isinstance(a, Atom) for a in by_identity)
    assert all(a.identity == "val-dwa-law-fem" for a in by_identity)
    assert all(isinstance(a.ascended, bool) for a in by_identity)

    narrowed = store.iter_atoms(identity="val-dwa-law-fem", seed=0)
    assert len(narrowed) == 1
    assert narrowed[0].seed == 0


def test_upsert_solution_is_idempotent_and_get_solution_reads_it_back(tmp_path):
    # Property 3: upsert_solution(...) twice -> exactly one row;
    # get_solution(digest) returns it; get_solution("missing") -> None.
    store, db_path = _new_store(tmp_path)
    _seed_solution(store)
    _seed_solution(store)

    conn = sqlite3.connect(db_path)
    row_count = conn.execute(
        "SELECT COUNT(*) FROM solutions WHERE digest = ?", (SOLUTION_DIGEST,)
    ).fetchone()[0]
    assert row_count == 1

    solution = store.get_solution(SOLUTION_DIGEST)
    assert solution is not None
    assert solution["digest"] == SOLUTION_DIGEST
    assert solution["owner"] == "sam"
    assert solution["commit_sha"] == "deadbeef"

    assert store.get_solution("missing") is None


def test_insert_atoms_raises_integrity_error_for_unregistered_solution(tmp_path):
    # Property 4: an atom whose solution_digest isn't in solutions raises
    # sqlite3.IntegrityError (FK), proving PRAGMA foreign_keys=ON actually
    # took effect on this connection.
    store, _ = _new_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_atoms([_atom()])


def test_insert_atoms_rolls_back_the_whole_call_on_a_mid_batch_fk_violation(tmp_path):
    # Regression (fix round 1): insert_atoms used to INSERT OR IGNORE each
    # atom in a loop then commit() once at the end with no rollback, so a
    # mid-batch FK violation left the earlier-in-this-call rows pending
    # (uncommitted but not rolled back either) on the long-lived
    # connection -- a *later*, unrelated commit (e.g. from add_lineage)
    # would silently flush them permanently, even though insert_atoms
    # itself raised and returned no count. insert_atoms must be atomic per
    # call: nothing from a raised call is ever observable, not even after
    # a later unrelated commit.
    store, _ = _new_store(tmp_path)
    _seed_solution(store)
    missing_solution = "sha256:never-registered"
    batch = [
        _atom(seed=0),
        _atom(seed=1),
        _atom(seed=2, solution_digest=missing_solution),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_atoms(batch)

    # Immediately: the first two rows must not have leaked in as pending.
    assert len(store.iter_atoms(solution_digest=SOLUTION_DIGEST)) == 0

    # An unrelated committing write must not flush the rolled-back rows
    # either -- this is the exact leak the reviewer reproduced.
    _seed_solution(store, digest="sha256:solution-b")
    assert len(store.iter_atoms(solution_digest=SOLUTION_DIGEST)) == 0


def test_add_lineage_is_idempotent(tmp_path):
    # Property 5.
    store, db_path = _new_store(tmp_path)
    _seed_solution(store)
    store.add_lineage(SOLUTION_DIGEST, "external-base-digest", "parent")
    store.add_lineage(SOLUTION_DIGEST, "external-base-digest", "parent")

    conn = sqlite3.connect(db_path)
    lineage_count = conn.execute(
        "SELECT COUNT(*) FROM lineage WHERE child_digest = ? AND parent_digest = ? AND kind = ?",
        (SOLUTION_DIGEST, "external-base-digest", "parent"),
    ).fetchone()[0]
    assert lineage_count == 1


def test_iter_atoms_rejects_unknown_filter_keys(tmp_path):
    # Property 6: an unknown filter key raises rather than being silently
    # ignored (which would otherwise return an unfiltered scan).
    store, _ = _new_store(tmp_path)
    with pytest.raises(ValueError, match="bogus"):
        store.iter_atoms(bogus="x")


def test_init_schema_provisions_but_does_not_populate_derived_view_tables(tmp_path):
    # Property 7: init_schema creates attainment/attainment_holders/
    # elite_pool (querying a nonexistent table would raise), but Task 5
    # never writes to them -- Tasks 7-8 own that.
    _, db_path = _new_store(tmp_path)
    conn = sqlite3.connect(db_path)
    for table in ("attainment", "attainment_holders", "elite_pool"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0


def test_poll_upsert_is_one_row_per_voter_and_replaces(tmp_path):
    store = Store(tmp_path / "poll.db")
    store.init_schema()
    store.upsert_poll_vote("v1", method="programs", timeline="2035",
                           roles=["mlr", "player"], xp="ascended")
    store.upsert_poll_vote("v1", method="hybrid", timeline="2040",
                           roles=["player"], xp="serious")  # same voter -> replace
    votes = store.iter_poll_votes()
    assert votes == [{"method": "hybrid", "timeline": "2040",
                      "roles": ["player"], "xp": "serious"}]  # one row, latest values


def test_poll_iter_is_anonymized_and_roundtrips_roles(tmp_path):
    store = Store(tmp_path / "poll.db")
    store.init_schema()
    store.upsert_poll_vote("a", method="llm", timeline="2030", roles=[], xp=None)
    store.upsert_poll_vote("b", method="rl", timeline="never",
                           roles=["eng", "enth"], xp="never")
    votes = store.iter_poll_votes()
    assert len(votes) == 2
    assert all(set(v) == {"method", "timeline", "roles", "xp"} for v in votes)  # no voter_id/ts
    b = next(v for v in votes if v["method"] == "rl")
    assert b["roles"] == ["eng", "enth"] and b["xp"] == "never"
    a = next(v for v in votes if v["method"] == "llm")
    assert a["roles"] == [] and a["xp"] is None


from nethackers.hub.ids import program_id


def _seed_one(store, digest="github.com/o/r@abc123"):
    store.upsert_solution(digest, repo="github.com/o/r", commit_sha="abc123",
                          owner="sam", root=".", entrypoint="bot.py",
                          registered_at="2026-01-01T00:00:00Z")
    return digest


def test_upsert_stamps_program_id_and_lookup_round_trips(tmp_path):
    store = Store(tmp_path / "h.sqlite3"); store.init_schema()
    digest = _seed_one(store)
    pid = program_id(digest)
    assert store.digest_for_program_id(pid) == digest
    assert store.digest_for_program_id("prog_doesnotexist") is None


def test_migration_backfills_legacy_null_rows(tmp_path):
    store = Store(tmp_path / "h.sqlite3"); store.init_schema()
    # Simulate a legacy row written before program_id existed.
    store.conn.execute(
        "INSERT INTO solutions (digest, repo, commit_sha, owner, root, entrypoint, registered_at)"
        " VALUES ('github.com/o/r@legacy', 'github.com/o/r', 'legacy', 'sam', '.', 'bot.py', 'x')")
    store.conn.execute("UPDATE solutions SET program_id = NULL WHERE digest = 'github.com/o/r@legacy'")
    store.conn.commit()

    store.init_schema()  # idempotent re-run must backfill the NULL row by pure function

    assert store.digest_for_program_id(program_id("github.com/o/r@legacy")) == "github.com/o/r@legacy"
