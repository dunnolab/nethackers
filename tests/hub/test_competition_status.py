"""Tests for the collective status-card metrics."""

from __future__ import annotations

from datetime import UTC, datetime

from nethackers.contracts.models import Atom
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.competition_status import read_competition_status


def _atom(digest: str, identity: str, progression: float, *, seed: int = 1) -> Atom:
    return Atom(
        solution_digest=digest,
        owner="hacker",
        tier="self-reported",
        identity=identity,
        seed=seed,
        progression=progression,
        milestone="Dlvl:3",
        ascended=False,
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
    }


def test_collective_frontier_empty_store_is_zero(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    assert read_competition_status(store) == {
        "community_frontier": 0.0,
        "frontier_gain_7d": 0.0,
        "identities_improved_7d": 0,
    }
