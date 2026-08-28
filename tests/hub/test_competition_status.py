"""Tests for the collective status-card metrics."""

from __future__ import annotations

from datetime import UTC, datetime

from nethackers.contracts.models import Atom
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.competition_status import read_competition_status


def _atom(
    digest: str,
    identity: str,
    progression: float,
    *,
    seed: int = 1,
    owner: str = "hacker",
    ascended: bool = False,
) -> Atom:
    return Atom(
        solution_digest=digest,
        owner=owner,
        tier="self-reported",
        identity=identity,
        seed=seed,
        progression=progression,
        milestone="Dlvl:3",
        ascended=ascended,
        status="completed",
        turns=1,
        steps=1,
        evaluator_image="img",
    )


def test_collective_frontier_uses_baseline_floor_and_seven_day_window(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    store.insert_baseline_atoms([
        _atom("autoascend", identity, 0.1) for identity in IDENTITIES
    ])
    for digest in ("old", "new-a", "new-b"):
        store.upsert_solution(
            digest=digest,
            repo="github.com/a/b",
            commit_sha=digest,
            owner="hacker",
            root=".",
            entrypoint="bot.py",
            registered_at="2026-08-28T00:00:00+00:00",
        )

    identity_a, identity_b = IDENTITIES[:2]
    store.insert_atoms([
        _atom("old", identity_a, 0.2),
        _atom("new-a", identity_a, 0.3),
        _atom("new-b", identity_b, 0.15),
    ])
    store.conn.execute(
        "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
        ("2026-08-20T00:00:00+00:00", "old"),
    )
    store.conn.execute(
        "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
        ("2026-08-27T00:00:00+00:00", "new-a"),
    )
    store.conn.execute(
        "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
        ("2026-08-28T00:00:00+00:00", "new-b"),
    )
    store.conn.commit()

    result = read_competition_status(
        store,
        now=datetime(2026, 8, 28, 12, tzinfo=UTC),
    )

    assert result == {
        "community_frontier": round(7.55 / 73, 6),
        "frontier_gain_7d": round(0.15 / 73, 6),
        "identities_improved_7d": 2,
        "largest_lift": {
            "identity": identity_a,
            "owner": "hacker",
            "solution_digest": "new-a",
            "score": 0.3,
            "baseline": 0.1,
            "lift": 0.2,
        },
    }


def test_collective_frontier_empty_store_is_zero(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    assert read_competition_status(store) == {
        "community_frontier": 0.0,
        "frontier_gain_7d": 0.0,
        "identities_improved_7d": 0,
        "largest_lift": None,
    }


def test_largest_lift_uses_identity_leaderboard_tiebreakers(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    identity = IDENTITIES[0]
    store.insert_baseline_atoms([_atom("autoascend", identity, 0.1)])

    for digest, owner, ascended, registered_at in (
        ("older", "alice", False, "2026-08-01T00:00:00+00:00"),
        ("ascended", "bob", True, "2026-08-02T00:00:00+00:00"),
    ):
        store.upsert_solution(
            digest=digest,
            repo=f"github.com/{owner}/bot",
            commit_sha=digest,
            owner=owner,
            root=".",
            entrypoint="bot.py",
            registered_at=registered_at,
        )
        store.insert_atoms([
            _atom(digest, identity, 0.4, owner=owner, ascended=ascended)
        ])
        store.conn.execute(
            "UPDATE atoms SET created_at = ? WHERE solution_digest = ?",
            (registered_at, digest),
        )
    store.conn.commit()

    largest = read_competition_status(store)["largest_lift"]

    assert largest is not None
    assert largest["owner"] == "bob"
    assert largest["solution_digest"] == "ascended"
