"""Collective competition progress for the website status cards.

The community frontier is the mean, across all 73 identities, of the better
of AutoAscend's per-identity baseline and the best participant program seen by
the requested point in time.  Untouched identities therefore stay at the
baseline instead of disappearing from the aggregate.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _program_bests(rows: list[Any], *, cutoff: datetime | None) -> dict[str, float]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for digest, identity, progression, created_at in rows:
        if cutoff is not None and _timestamp(created_at) > cutoff:
            continue
        grouped.setdefault((digest, identity), []).append(float(progression))

    bests: dict[str, float] = {}
    for (_digest, identity), values in grouped.items():
        mean = statistics.mean(values)
        bests[identity] = max(bests.get(identity, 0.0), mean)
    return bests


def read_competition_status(
    store: Store,
    *,
    tier: str = "self-reported",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the collective frontier and its movement over the last 7 days."""
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    cutoff = current_time.astimezone(UTC) - timedelta(days=7)

    baseline_groups: dict[str, list[float]] = {}
    for atom in store.iter_baseline_atoms():
        baseline_groups.setdefault(atom.identity, []).append(float(atom.progression))
    baseline = {
        identity: statistics.mean(values)
        for identity, values in baseline_groups.items()
    }

    rows = store.conn.execute(
        "SELECT solution_digest, identity, progression, created_at "
        "FROM atoms WHERE tier = ?",
        (tier,),
    ).fetchall()
    current_bests = _program_bests(rows, cutoff=None)
    previous_bests = _program_bests(rows, cutoff=cutoff)

    current_values: list[float] = []
    previous_values: list[float] = []
    improved = 0
    for identity in IDENTITIES:
        floor = baseline.get(identity, 0.0)
        current = max(floor, current_bests.get(identity, 0.0))
        previous = max(floor, previous_bests.get(identity, 0.0))
        current_values.append(current)
        previous_values.append(previous)
        if current > previous + 1e-12:
            improved += 1

    current_frontier = statistics.mean(current_values)
    previous_frontier = statistics.mean(previous_values)
    return {
        "community_frontier": round(current_frontier, 6),
        "frontier_gain_7d": round(current_frontier - previous_frontier, 6),
        "identities_improved_7d": improved,
    }
