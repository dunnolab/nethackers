"""Tests for durable Wall of Fame recognition."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.wall_of_fame import read_wall_of_fame


def _atom(digest: str, owner: str, identity: str, progression: float) -> Atom:
    return Atom(
        solution_digest=digest,
        owner=owner,
        tier="self-reported",
        identity=identity,
        seed=1,
        progression=progression,
        milestone="Dlvl:3",
        ascended=False,
        status="completed",
        turns=1,
        steps=1,
        evaluator_image="img",
    )


def test_wall_tracks_current_keepers_and_historical_breakthroughs(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    identity_a, identity_b, identity_c = IDENTITIES[:3]
    store.insert_baseline_atoms([
        _atom("autoascend", "autoascend", identity_a, 0.1),
        _atom("autoascend", "autoascend", identity_b, 0.1),
        _atom("autoascend", "autoascend", identity_c, 0.2),
    ])
    submissions = [
        ("alice-a", "alice", identity_a, 0.2, "2026-08-01T00:00:00+00:00"),
        ("alice-a2", "alice", identity_a, 0.25, "2026-08-01T12:00:00+00:00"),
        ("bob-a", "bob", identity_a, 0.4, "2026-08-02T00:00:00+00:00"),
        ("carol-b", "carol", identity_b, 0.4, "2026-08-03T00:00:00+00:00"),
        ("dave-c", "dave", identity_c, 0.1, "2026-08-04T00:00:00+00:00"),
    ]
    for digest, owner, identity, progression, created_at in submissions:
        store.upsert_solution(
            digest=digest,
            repo=f"github.com/{owner}/bot",
            commit_sha=digest,
            owner=owner,
            root=".",
            entrypoint="bot.py",
            registered_at=created_at,
        )
        store.insert_atoms([_atom(digest, owner, identity, progression)])
        store.conn.execute(
            "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
            (created_at, digest),
        )
    store.conn.commit()

    wall = read_wall_of_fame(store)

    assert [(row["owner"], row["records"]) for row in wall["keepers"]] == [
        ("bob", 1),
        ("carol", 1),
    ]
    assert wall["keepers"][0]["total_lift"] == 0.3
    assert [row["owner"] for row in wall["breakthroughs"]] == [
        "carol",
        "bob",
        "alice",
        "alice",
    ]
    assert [row["gain"] for row in wall["breakthroughs"]] == [0.3, 0.15, 0.05, 0.1]
    assert all(row["owner"] != "dave" for rows in wall.values() for row in rows)


def test_wall_is_empty_without_results(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    assert read_wall_of_fame(store) == {"keepers": [], "breakthroughs": []}
