"""Durable, achievement-based hacker recognition for the website.

Served at ``GET /recognition`` (the UI still heads the section "Wall of
Fame"). Two ledgers over the self-reported tier: the current identity
record-holders grouped by hacker ("keepers"), and every all-time one-step
frontier advance ("breakthroughs"). Breakthrough rows are program-bearing --
the opaque ``program_id`` + ``reference {repo, commit}``, never
``solution_digest`` -- per the hub API redesign, and the response carries the
envelope's ``generated_at`` as-of.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from nethackers.hub.ids import program_id
from nethackers.hub.store import Store
from nethackers.hub.views.source import Epoch, source_for

_MIN_LIFT = 0.0005


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_recognition(
    store: Store, *, limit: int = 100, tier: str = "self-reported",
    epoch: Epoch | None = None,
) -> dict[str, Any]:
    """Return current keepers and all-time one-step frontier breakthroughs for
    ``tier``. Both ledgers are measured against the floor that PAIRS with the
    tier (``views.source``), so a hidden-seed lift is never taken over the
    published-seed floor.

    An identity with **no floor in this tier is skipped entirely** -- see
    ``_floor_for`` below.
    """
    source = source_for(tier, epoch)
    baseline_groups: dict[str, list[float]] = {}
    for atom in source.iter_baseline_atoms(store):
        baseline_groups.setdefault(atom.identity, []).append(float(atom.progression))
    baseline = {
        identity: statistics.mean(values)
        for identity, values in baseline_groups.items()
    }

    # The LEFT JOIN carries each program's git pointer (repo, commit) alongside
    # its atoms so breakthrough rows can be program-bearing without an N+1 of
    # per-digest solution lookups; repo/commit are constant per solution_digest.
    tier_where, tier_params = source.where("a")
    rows = store.conn.execute(
        "SELECT a.solution_digest, a.owner, a.identity, a.progression, a.ascended, "
        "a.created_at, s.repo, s.commit_sha "
        f"FROM {source.atoms_table} a LEFT JOIN solutions s ON a.solution_digest = s.digest "
        f"WHERE {tier_where}",
        tier_params,
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for digest, owner, identity, progression, ascended, created_at, repo, commit in rows:
        key = (digest, identity)
        item = grouped.setdefault(
            key,
            {
                "digest": digest,
                "owner": owner,
                "identity": identity,
                "repo": repo,
                "commit": commit,
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
        floor = baseline.get(identity)
        if floor is None:
            # No floor in this tier means no lift can be computed. A lift with
            # no floor is not a small lift -- it is not a measurement. Crediting
            # it against 0.0 would hand this hacker the program's entire score.
            continue
        winner = sorted(
            candidates,
            key=lambda row: (
                -row["score"], -row["ascensions"], row["earliest"], row["digest"]
            ),
        )[0]
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
        frontier = baseline.get(identity)
        if frontier is None:
            continue        # same rule: no floor, no claim of an advance
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
                "program_id": program_id(result["digest"]),
                "reference": {"repo": result["repo"], "commit": result["commit"]},
                "at": result["at"].isoformat(),
            }
            breakthroughs.append(event)
            frontier = result["score"]

    breakthroughs.sort(key=lambda row: (row["at"], row["owner"]), reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "keepers": keepers[:limit],
        "breakthroughs": breakthroughs[:limit],
    }
