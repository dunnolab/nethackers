"""Tests for durable Wall of Fame recognition.

The ledger is one log of frontier advances, read three ways: ``recent``
(newest first), ``breakthroughs`` (biggest first) and ``contributors``
(each hacker's advances summed). Credit follows when the hacker REGISTERED
the program, never when a verifier got round to scoring it.
"""

from __future__ import annotations

import pytest

from nethackers.contracts.models import Atom
from nethackers.hub.ids import program_id
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.recognition import read_recognition


def _atom(
    digest: str,
    owner: str,
    identity: str,
    progression: float,
    *,
    ascended: bool = False,
) -> Atom:
    return Atom(
        solution_digest=digest,
        owner=owner,
        tier="self-reported",
        identity=identity,
        seed=1,
        progression=progression,
        milestone="Dlvl:3",
        ascended=ascended,
        status="completed",
        turns=1,
        steps=1,
        evaluator_image="img",
    )


def _store(tmp_path) -> Store:
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    return store


def _register(
    store: Store,
    digest: str,
    owner: str,
    identity: str,
    progression: float,
    *,
    registered_at: str | None,
    atom_at: str | None = None,
    ascended: bool = False,
) -> None:
    """Register one program and score it on one identity. ``registered_at`` is
    the hacker's clock, ``atom_at`` the scorer's -- separable on purpose, since
    the replay must follow the former. ``registered_at=None`` writes the row and
    then clears the column, which is how a legacy row really comes to hold NULL;
    ``upsert_solution`` itself still requires one."""
    store.upsert_solution(
        digest=digest,
        repo=f"github.com/{owner}/bot",
        commit_sha=digest,
        owner=owner,
        root=".",
        entrypoint="bot.py",
        registered_at=registered_at or (atom_at or ""),
    )
    if registered_at is None:
        store.conn.execute(
            "UPDATE solutions SET registered_at = NULL WHERE digest = ?", (digest,)
        )
    store.insert_atoms([_atom(digest, owner, identity, progression, ascended=ascended)])
    store.conn.execute(
        "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
        (atom_at or registered_at, digest),
    )
    store.conn.commit()


def test_the_log_credits_every_advance_and_surpassed_hackers_keep_theirs(tmp_path):
    store = _store(tmp_path)
    identity_a, identity_b, identity_c = IDENTITIES[:3]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.1),
        _atom("autoascend", "autoascend", identity_c, 0.2),
    ])
    for digest, owner, identity, progression, at in [
        ("alice-a", "alice", identity_a, 0.2, "2026-08-01T00:00:00+00:00"),
        ("alice-a2", "alice", identity_a, 0.25, "2026-08-01T12:00:00+00:00"),
        ("bob-a", "bob", identity_a, 0.4, "2026-08-02T00:00:00+00:00"),
        ("carol-b", "carol", identity_b, 0.4, "2026-08-03T00:00:00+00:00"),
        ("dave-c", "dave", identity_c, 0.1, "2026-08-04T00:00:00+00:00"),
    ]:
        _register(store, digest, owner, identity, progression, registered_at=at)

    wall = read_recognition(store)

    # alice was surpassed by bob on identity_a and still keeps her two advances:
    # 0.1 -> 0.2 -> 0.25. Holding nothing at the end costs her nothing.
    assert [(row["owner"], row["impact"]) for row in wall["contributors"]] == [
        ("carol", 0.3),
        ("alice", 0.15),
        ("bob", 0.15),
    ]
    assert [row["advances"] for row in wall["contributors"]] == [1, 2, 1]
    assert [row["identities"] for row in wall["contributors"]] == [1, 1, 1]
    assert wall["contributors"][0]["roles"] == [identity_b.split("-", 1)[0]]
    # dave landed below the floor: no advance, so no credit anywhere.
    assert all(
        row["owner"] != "dave"
        for row in wall["contributors"] + wall["breakthroughs"] + wall["recent"]
    )


def test_impact_telescopes_to_the_whole_community_climb(tmp_path):
    """Every advance is measured against the frontier immediately before it, so
    the hackers' impact sums to exactly the distance the community moved."""
    store = _store(tmp_path)
    identity_a, identity_b = IDENTITIES[:2]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.2),
    ])
    for digest, owner, identity, progression, at in [
        ("a1", "alice", identity_a, 0.3, "2026-08-01T00:00:00+00:00"),
        ("b1", "bob", identity_a, 0.55, "2026-08-02T00:00:00+00:00"),
        ("a2", "alice", identity_a, 0.6, "2026-08-03T00:00:00+00:00"),
        ("c1", "carol", identity_b, 0.5, "2026-08-04T00:00:00+00:00"),
    ]:
        _register(store, digest, owner, identity, progression, registered_at=at)

    wall = read_recognition(store)

    climb = (0.6 - 0.1) + (0.5 - 0.2)
    assert sum(row["impact"] for row in wall["contributors"]) == pytest.approx(climb)
    assert sum(row["gain"] for row in wall["recent"]) == pytest.approx(climb)


def test_breakthroughs_rank_by_the_size_of_the_advance(tmp_path):
    store = _store(tmp_path)
    identity_a, identity_b = IDENTITIES[:2]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.1),
    ])
    for digest, owner, identity, progression, at in [
        ("small", "alice", identity_a, 0.15, "2026-08-01T00:00:00+00:00"),
        ("huge", "bob", identity_a, 0.55, "2026-08-02T00:00:00+00:00"),
        ("middle", "carol", identity_b, 0.3, "2026-08-03T00:00:00+00:00"),
    ]:
        _register(store, digest, owner, identity, progression, registered_at=at)

    wall = read_recognition(store)

    assert [(row["owner"], row["gain"]) for row in wall["breakthroughs"]] == [
        ("bob", 0.4),
        ("carol", 0.2),
        ("alice", 0.05),
    ]
    # The same events, newest first -- a log of what just happened.
    assert [row["owner"] for row in wall["recent"]] == ["carol", "bob", "alice"]
    # Rows are program-bearing: opaque program_id + reference, never the digest
    # (hub API redesign).
    assert [row["program_id"] for row in wall["breakthroughs"]] == [
        program_id("huge"),
        program_id("middle"),
        program_id("small"),
    ]
    assert wall["breakthroughs"][0]["reference"] == {
        "repo": "github.com/bob/bot",
        "commit": "huge",
    }
    assert all("solution_digest" not in row for row in wall["breakthroughs"])


def test_equal_advances_rank_the_one_who_got_there_first_above(tmp_path):
    store = _store(tmp_path)
    identity_a, identity_b = IDENTITIES[:2]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.1),
    ])
    for digest, owner, identity, at in [
        ("later", "bob", identity_a, "2026-08-09T00:00:00+00:00"),
        ("first", "alice", identity_b, "2026-08-02T00:00:00+00:00"),
    ]:
        _register(store, digest, owner, identity, 0.3, registered_at=at)

    wall = read_recognition(store)

    assert [row["gain"] for row in wall["breakthroughs"]] == [0.2, 0.2]
    assert [row["owner"] for row in wall["breakthroughs"]] == ["alice", "bob"]


def test_the_replay_follows_registration_not_the_scorer_clock(tmp_path):
    """The private tier's ``created_at`` is when our verifier reached a program,
    which is not when the hacker got there. Credit must follow the hacker."""
    store = _store(tmp_path)
    identity = IDENTITIES[0]
    store.insert_baseline_atoms([_atom("autoascend", "autoascend", identity, 0.1)])
    # alice registered FIRST and was scored LAST; bob the other way round.
    _register(store, "alice-1", "alice", identity, 0.5,
              registered_at="2026-08-01T00:00:00+00:00",
              atom_at="2026-09-01T00:00:00+00:00")
    _register(store, "bob-1", "bob", identity, 0.3,
              registered_at="2026-08-02T00:00:00+00:00",
              atom_at="2026-08-02T00:00:00+00:00")

    wall = read_recognition(store)

    # Replayed by registration, alice took the identity 0.1 -> 0.5 outright and
    # bob's 0.3 advanced nothing. Replayed by the scorer's clock it would read
    # bob 0.1 -> 0.3 then alice 0.3 -> 0.5, handing bob a slice of her climb.
    assert [(row["owner"], row["impact"]) for row in wall["contributors"]] == [
        ("alice", 0.4)
    ]
    assert [(row["owner"], row["gain"]) for row in wall["breakthroughs"]] == [
        ("alice", 0.4)
    ]
    assert wall["recent"][0]["at"] == "2026-08-01T00:00:00+00:00"


def test_a_program_with_no_registration_time_falls_back_to_its_atoms(tmp_path):
    """A legacy row with a NULL ``registered_at`` is still an advance; order it
    by the only clock it has rather than dropping it."""
    store = _store(tmp_path)
    identity = IDENTITIES[0]
    store.insert_baseline_atoms([_atom("autoascend", "autoascend", identity, 0.1)])
    _register(store, "legacy", "alice", identity, 0.3,
              registered_at=None, atom_at="2026-08-01T00:00:00+00:00")
    _register(store, "fresh", "bob", identity, 0.4,
              registered_at="2026-08-02T00:00:00+00:00")

    wall = read_recognition(store)

    assert [(row["owner"], row["gain"]) for row in wall["recent"]] == [
        ("bob", 0.1),
        ("alice", 0.2),
    ]


def test_each_view_is_limited_on_its_own_ordering(tmp_path):
    """``breakthroughs`` and ``recent`` are separate top-N slices of the whole
    log: the biggest two and the newest two are different pairs."""
    store = _store(tmp_path)
    identities = IDENTITIES[:3]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity, 0.1) for identity in identities
    ])
    for digest, owner, identity, progression, at in [
        ("big", "alice", identities[0], 0.9, "2026-08-01T00:00:00+00:00"),
        ("mid", "bob", identities[1], 0.5, "2026-08-02T00:00:00+00:00"),
        ("small", "carol", identities[2], 0.2, "2026-08-03T00:00:00+00:00"),
    ]:
        _register(store, digest, owner, identity, progression, registered_at=at)

    wall = read_recognition(store, limit=2)

    assert [row["owner"] for row in wall["breakthroughs"]] == ["alice", "bob"]
    assert [row["owner"] for row in wall["recent"]] == ["carol", "bob"]
    assert len(wall["contributors"]) == 2


def test_a_hacker_who_moved_several_identities_counts_each_one(tmp_path):
    store = _store(tmp_path)
    identity_a, identity_b = IDENTITIES[0], IDENTITIES[20]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.1),
    ])
    at = "2026-08-01T00:00:00+00:00"
    store.upsert_solution(
        digest="both", repo="github.com/alice/bot", commit_sha="both", owner="alice",
        root=".", entrypoint="bot.py", registered_at=at,
    )
    store.insert_atoms([
        _atom("both", "alice", identity_a, 0.4),
        _atom("both", "alice", identity_b, 0.3),
    ])
    store.conn.execute("UPDATE atoms SET created_at = ?", (at,))
    store.conn.commit()

    wall = read_recognition(store)

    contributor = wall["contributors"][0]
    assert contributor["impact"] == 0.5
    assert contributor["advances"] == 2
    assert contributor["identities"] == 2
    assert contributor["roles"] == sorted(
        {identity_a.split("-", 1)[0], identity_b.split("-", 1)[0]}
    )


def test_roles_lead_with_where_the_hacker_actually_moved_the_frontier(tmp_path):
    """The website prints the first few roles and counts the rest, so the order is
    load-bearing: alphabetical would hide a Wizard specialist behind "arc"."""
    store = _store(tmp_path)
    arc, wiz = IDENTITIES[0], IDENTITIES[-1]
    assert arc.startswith("arc-") and wiz.startswith("wiz-")
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", arc, 0.1),
        _atom("autoascend", "autoascend", wiz, 0.1),
    ])
    _register(store, "small", "alice", arc, 0.15,
              registered_at="2026-08-01T00:00:00+00:00")
    _register(store, "big", "alice", wiz, 0.6,
              registered_at="2026-08-02T00:00:00+00:00")

    wall = read_recognition(store)

    assert wall["contributors"][0]["roles"] == ["wiz", "arc"]


def test_the_wall_is_empty_without_results(tmp_path):
    store = _store(tmp_path)
    result = read_recognition(store)
    assert result.pop("generated_at")  # ISO 8601 as-of, present on every response
    assert result == {"contributors": [], "breakthroughs": [], "recent": []}
