"""Durable, achievement-based recognition for the website Wall of Fame."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from nethackers.hub.store import Store

_MIN_LIFT = 0.0005


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_wall_of_fame(store: Store, *, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
    """Return current keepers and all-time one-step frontier breakthroughs.

    Recognition is deliberately based on the self-reported competition tier,
    independently of whichever tier a visitor is viewing in the Frontier UI.
    """
    baseline_groups: dict[str, list[float]] = {}
    for atom in store.iter_baseline_atoms():
        baseline_groups.setdefault(atom.identity, []).append(float(atom.progression))
    baseline = {
        identity: statistics.mean(values)
        for identity, values in baseline_groups.items()
    }

    rows = store.conn.execute(
        "SELECT solution_digest, owner, identity, progression, ascended, created_at "
        "FROM atoms WHERE tier = 'self-reported'"
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for digest, owner, identity, progression, ascended, created_at in rows:
        key = (digest, identity)
        item = grouped.setdefault(
            key,
            {
                "digest": digest,
                "owner": owner,
                "identity": identity,
                "values": [],
                "ascensions": 0,
                "at": _timestamp(created_at),
                "earliest": _timestamp(created_at),
            },
        )
        item["values"].append(float(progression))
        item["ascensions"] += int(ascended)
        item["at"] = max(item["at"], _timestamp(created_at))
        item["earliest"] = min(item["earliest"], _timestamp(created_at))

    results: list[dict[str, Any]] = []
    for item in grouped.values():
        values = item.pop("values")
        results.append({**item, "score": statistics.mean(values)})

    # Current keepers: one best participant per identity, but only where that
    # participant is genuinely above the AutoAscend floor.
    by_identity: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_identity.setdefault(result["identity"], []).append(result)

    keepers_by_owner: dict[str, dict[str, Any]] = {}
    for identity, candidates in by_identity.items():
        winner = sorted(
            candidates,
            key=lambda row: (
                -row["score"], -row["ascensions"], row["earliest"], row["digest"]
            ),
        )[0]
        floor = baseline.get(identity, 0.0)
        if winner["score"] <= floor + _MIN_LIFT:
            continue
        keeper = keepers_by_owner.setdefault(
            winner["owner"],
            {"owner": winner["owner"], "identities": [], "roles": set(), "total_lift": 0.0},
        )
        keeper["identities"].append(identity)
        keeper["roles"].add(identity.split("-", 1)[0])
        keeper["total_lift"] += winner["score"] - floor

    keepers = [
        {
            "owner": item["owner"],
            "records": len(item["identities"]),
            "identities": sorted(item["identities"]),
            "roles": sorted(item["roles"]),
            "total_lift": round(item["total_lift"], 6),
        }
        for item in keepers_by_owner.values()
    ]
    keepers.sort(key=lambda row: (-row["records"], -row["total_lift"], row["owner"]))

    # Greatest breakthroughs: replay each identity chronologically, beginning
    # at AutoAscend. Credit only the amount by which a result moved the frontier
    # beyond the best result that existed immediately before it.
    breakthroughs: list[dict[str, Any]] = []
    for identity, candidates in by_identity.items():
        frontier = baseline.get(identity, 0.0)
        for result in sorted(candidates, key=lambda row: (row["at"], row["digest"])):
            if result["score"] <= frontier + _MIN_LIFT:
                continue
            gain = result["score"] - frontier
            event = {
                "owner": result["owner"],
                "identity": identity,
                "gain": round(gain, 6),
                "score": round(result["score"], 6),
                "previous": round(frontier, 6),
                "solution_digest": result["digest"],
                "at": result["at"].isoformat(),
            }
            breakthroughs.append(event)
            frontier = result["score"]

    breakthroughs.sort(key=lambda row: (row["at"], row["owner"]), reverse=True)
    return {"keepers": keepers[:limit], "breakthroughs": breakthroughs[:limit]}
