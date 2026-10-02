"""Durable, achievement-based hacker recognition for the website.

Served at ``GET /recognition`` (the UI heads the section "Hackers moving the
frontier"). ONE ledger -- every all-time one-step frontier advance -- read
three ways, computed for whichever tier is requested and measured against the
floor that pairs with that tier (``views.source``):

* ``recent`` -- the advances newest first: what just happened.
* ``breakthroughs`` -- the same advances biggest first: what mattered most.
* ``contributors`` -- each hacker's advances summed into their total impact.

``contributors`` deliberately credits a hacker for the ground they moved, not
for ground they still hold: being surpassed later costs nothing. Because every
advance is measured against the frontier immediately before it, the credit
telescopes -- all of the hackers' impact sums to exactly the distance the
community moved above AutoAscend, with no slice counted twice.

Credit follows WHEN THE HACKER REGISTERED the program
(``solutions.registered_at``), never when a scorer reached it. On the verified
tier an atom's ``created_at`` is when our verifier got round to that program,
and replaying in that order hands part of an earlier hacker's climb to whoever
happened to be verified first. A program with no registration time at all
(legacy rows) falls back to its earliest atom: the only clock it has.

Advance rows are program-bearing -- the opaque ``program_id`` + ``reference
{repo, commit}``, never ``solution_digest`` -- per the hub API redesign, and
the response carries the envelope's ``generated_at`` as-of.
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
    """Return the all-time log of one-step frontier advances for ``tier``, as
    ``recent`` (newest first), ``breakthroughs`` (biggest first) and
    ``contributors`` (per-hacker totals). Every ledger is measured against the
    floor that PAIRS with the tier (``views.source``), so a hidden-seed lift is
    never taken over the published-seed floor.

    An identity with no floor in this tier is skipped entirely, so it credits
    nobody in any of the three. A lift with no floor is not a small lift; it is
    not a measurement, and crediting it against 0.0 would hand the hacker the
    program's entire score. The check (``baseline.get(identity) is None``) is
    applied inline at the top of the replay, before that identity's candidates
    are considered.

    ``limit`` caps each ledger on ITS OWN ordering -- the biggest advances and
    the newest advances are different sets, so slicing one pre-sorted list for
    both would make "newest" mean "newest among the biggest ``limit``".
    """
    source = source_for(tier, epoch)
    baseline_groups: dict[str, list[float]] = {}
    for atom in source.iter_baseline_atoms(store):
        baseline_groups.setdefault(atom.identity, []).append(float(atom.progression))
    baseline = {
        identity: statistics.mean(values)
        for identity, values in baseline_groups.items()
    }

    # The LEFT JOIN carries each program's git pointer (repo, commit) and its
    # registration time alongside its atoms, so advance rows can be
    # program-bearing and replayed on the hacker's clock without an N+1 of
    # per-digest solution lookups; all three are constant per solution_digest.
    tier_where, tier_params = source.where("a")
    rows = store.conn.execute(
        "SELECT a.solution_digest, a.owner, a.identity, a.progression, "
        "a.created_at, s.repo, s.commit_sha, s.registered_at "
        f"FROM {source.atoms_table} a LEFT JOIN solutions s ON a.solution_digest = s.digest "
        f"WHERE {tier_where}",
        tier_params,
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for digest, owner, identity, progression, created_at, repo, commit, registered in rows:
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
                "registered": registered,
                "scored": _timestamp(created_at),
            },
        )
        item["values"].append(float(progression))
        item["scored"] = min(item["scored"], _timestamp(created_at))

    results: list[dict[str, Any]] = []
    for item in grouped.values():
        values = item.pop("values")
        scored = item.pop("scored")
        registered = item.pop("registered")
        results.append({
            **item,
            "at": _timestamp(registered) if registered else scored,
            "score": statistics.mean(values),
        })

    by_identity: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_identity.setdefault(result["identity"], []).append(result)

    # Replay each identity in registration order, beginning at AutoAscend.
    # Credit only the amount by which a result moved the frontier beyond the
    # best result that existed immediately before it.
    events: list[dict[str, Any]] = []
    for identity, candidates in by_identity.items():
        frontier = baseline.get(identity)
        if frontier is None:
            continue        # no floor, no claim of an advance
        for result in sorted(candidates, key=lambda row: (row["at"], row["digest"])):
            if result["score"] <= frontier + _MIN_LIFT:
                continue
            events.append({
                "owner": result["owner"],
                "identity": identity,
                "gain": round(result["score"] - frontier, 6),
                "score": round(result["score"], 6),
                "previous": round(frontier, 6),
                "program_id": program_id(result["digest"]),
                "reference": {"repo": result["repo"], "commit": result["commit"]},
                "at": result["at"],
            })
            frontier = result["score"]

    # Total impact per hacker: the sum of the advances above, exactly as the
    # log prints them, so a reader can add the rows up and arrive here.
    #
    # ``roles`` is ordered by how much of that hacker's impact each role carries,
    # not alphabetically: the website shows the first few and counts the rest, and
    # an alphabetical list would hide a Valkyrie specialist behind "Archeologist".
    tally: dict[str, dict[str, Any]] = {}
    for event in events:
        row = tally.setdefault(
            event["owner"],
            {"owner": event["owner"], "impact": 0.0, "advances": 0,
             "identities": set(), "roles": {}},
        )
        row["impact"] += event["gain"]
        row["advances"] += 1
        row["identities"].add(event["identity"])
        role = event["identity"].split("-", 1)[0]
        row["roles"][role] = row["roles"].get(role, 0.0) + event["gain"]
    contributors = [
        {
            "owner": row["owner"],
            "impact": round(row["impact"], 6),
            "advances": row["advances"],
            "identities": len(row["identities"]),
            "roles": [
                role for role, _ in
                sorted(row["roles"].items(), key=lambda item: (-item[1], item[0]))
            ],
        }
        for row in tally.values()
    ]
    contributors.sort(key=lambda row: (-row["impact"], -row["advances"], row["owner"]))

    breakthroughs = sorted(events, key=lambda row: (-row["gain"], row["at"], row["owner"]))
    # Newest first, ties by owner ASCENDING: sorting one reversed tuple would
    # reverse the owner too. Two stable passes, the weaker key first.
    recent = sorted(events, key=lambda row: row["owner"])
    recent.sort(key=lambda row: row["at"], reverse=True)

    def _wire(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "at": row["at"].isoformat()} for row in rows[:limit]]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "contributors": contributors[:limit],
        "breakthroughs": _wire(breakthroughs),
        "recent": _wire(recent),
    }
