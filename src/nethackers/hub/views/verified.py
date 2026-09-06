"""Verified tier aggregate read: per-identity aggregates over verified_atoms
(mean progression, deepest milestone reached, episode count). Same milestone
ordering (ACHIEVEMENTS) the attainment view uses. Also provides verification_status
helper to report per-solution verification progress."""

from __future__ import annotations

import statistics
from typing import Any

from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.milestones import deepest_milestone


def read_verified(
    store: Store, *, secret_fingerprint: str, seeds, evaluator_image: str
) -> dict[str, Any]:
    """Read verified-tier aggregates filtered to current secret fingerprint,
    evaluator image, and seed set. Returns per-identity aggregates (progression
    mean, deepest milestone, episode count) and overall progression mean."""
    seed_set = frozenset(seeds)
    atoms = [
        a
        for a in store.iter_verified_atoms(
            secret_fingerprint=secret_fingerprint, evaluator_image=evaluator_image
        )
        if a.seed in seed_set
    ]
    per: dict[str, list] = {}
    for a in atoms:
        per.setdefault(a.identity, []).append(a)
    per_identity = {
        ident: {
            "progression": round(statistics.mean(x.progression for x in g), 3),
            "deepest": deepest_milestone([x.milestone for x in g]),
            "episodes": len(g),
        }
        for ident, g in per.items()
    }
    overall: float | None = None
    if per_identity:
        progressions = [c["progression"] for c in per_identity.values()]
        overall = round(statistics.mean(progressions), 3)
    return {"per_identity": per_identity, "overall": overall}


def verification_status(
    store: Store,
    solution_digest: str,
    *,
    secret_fingerprint: str,
    evaluator_image: str,
    seeds,
) -> dict[str, Any]:
    """Report verification progress for a solution: state machine
    (not_attempted→attempted→verified), done/total counts, and failure_kind."""
    seed_set = frozenset(seeds)
    total = len(IDENTITIES) * len(seeds)
    done = len(
        [
            a
            for a in store.iter_verified_atoms(
                solution_digest=solution_digest,
                secret_fingerprint=secret_fingerprint,
                evaluator_image=evaluator_image,
            )
            if a.seed in seed_set
        ]
    )
    if done >= total:
        return {"state": "verified", "done": done, "total": total, "failure_kind": None}
    latest = store.latest_verified_attempt(
        solution_digest,
        secret_fingerprint=secret_fingerprint,
        evaluator_image=evaluator_image,
    )
    if latest is not None:
        return {
            "state": "attempted",
            "done": done,
            "total": total,
            "failure_kind": latest["failure_kind"],
        }
    return {"state": "not_attempted", "done": done, "total": total, "failure_kind": None}
