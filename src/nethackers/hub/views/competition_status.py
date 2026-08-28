"""Collective competition progress for the website status cards.

The community frontier is the mean, across all 73 identities, of the better
of AutoAscend's per-identity baseline and the best participant program seen by
the requested point in time. Untouched identities therefore stay at the
baseline instead of disappearing from the aggregate. The response also names
the current identity result with the largest lift over its AutoAscend floor,
so every status card is backed by this one authoritative endpoint.
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


def _program_bests(rows: list[Any], *, cutoff: datetime | None) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for digest, owner, identity, progression, ascended, created_at in rows:
        created = _timestamp(created_at)
        if cutoff is not None and created > cutoff:
            continue
        item = grouped.setdefault(
            (digest, identity),
            {"owner": owner, "values": [], "ascensions": 0, "earliest": created},
        )
        item["values"].append(float(progression))
        item["ascensions"] += int(ascended)
        item["earliest"] = min(item["earliest"], created)

    bests: dict[str, dict[str, Any]] = {}
    for (digest, identity), item in grouped.items():
        candidate = {
            "solution_digest": digest,
            "owner": item["owner"],
            "score": statistics.mean(item["values"]),
            "ascensions": item["ascensions"],
            "earliest": item["earliest"],
        }
        current = bests.get(identity)
        if current is None or (
            -candidate["score"], -candidate["ascensions"], candidate["earliest"], digest
        ) < (
            -current["score"], -current["ascensions"], current["earliest"],
            current["solution_digest"]
        ):
            bests[identity] = candidate
    return bests


def read_competition_status(
    store: Store,
    *,
    tier: str = "self-reported",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return collective progress, seven-day movement, and the largest lift."""
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
        "SELECT solution_digest, owner, identity, progression, ascended, created_at "
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
        current_best = current_bests.get(identity)
        previous_best = previous_bests.get(identity)
        current = max(floor, current_best["score"] if current_best else 0.0)
        previous = max(floor, previous_best["score"] if previous_best else 0.0)
        current_values.append(current)
        previous_values.append(previous)
        if current > previous + 1e-12:
            improved += 1

    lifts = [
        {
            "identity": identity,
            "owner": result["owner"],
            "solution_digest": result["solution_digest"],
            "score": result["score"],
            "baseline": baseline[identity],
            "lift": result["score"] - baseline[identity],
        }
        for identity, result in current_bests.items()
        if identity in baseline and result["score"] > baseline[identity] + 1e-12
    ]
    largest = min(
        lifts,
        key=lambda row: (-row["lift"], row["identity"], row["solution_digest"]),
        default=None,
    )
    if largest is not None:
        largest = {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in largest.items()
        }

    current_frontier = statistics.mean(current_values)
    previous_frontier = statistics.mean(previous_values)
    return {
        "community_frontier": round(current_frontier, 6),
        "frontier_gain_7d": round(current_frontier - previous_frontier, 6),
        "identities_improved_7d": improved,
        "largest_lift": largest,
    }
