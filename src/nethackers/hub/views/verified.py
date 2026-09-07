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


def _aggregate(atoms, seeds) -> dict[str, Any]:
    """Fold hidden-seed atoms into ``{per_identity, overall}``, keeping only
    seeds still in the current hidden list. ``overall`` is the mean OF THE
    PER-IDENTITY MEANS (not of raw episodes), so an identity with more
    episodes recorded doesn't weigh more heavily than the rest.

    ``overall`` is ``None``, never ``0.0``, when nothing has been measured --
    "not computed yet" and "scored zero" are different claims, and conflating
    them would show every program beating an uncomputed floor."""
    seed_set = frozenset(seeds)
    per: dict[str, list] = {}
    for a in atoms:
        if a.seed in seed_set:
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


def read_verified(
    store: Store, *, secret_fingerprint: str, seeds, evaluator_image: str
) -> dict[str, Any]:
    """Read verified-tier aggregates filtered to current secret fingerprint,
    evaluator image, and seed set. Returns per-identity aggregates (progression
    mean, deepest milestone, episode count) and overall progression mean."""
    return _aggregate(
        store.iter_verified_atoms(
            secret_fingerprint=secret_fingerprint, evaluator_image=evaluator_image
        ),
        seeds,
    )


def read_verified_baseline(
    store: Store, *, secret_fingerprint: str, seeds, evaluator_image: str
) -> dict[str, Any]:
    """Read AutoAscend's hidden-seed floor, scoped to the same epoch as
    ``read_verified`` and returned in the same shape -- so a board can put a
    program's verified number next to the floor's without reshaping either,
    and can never compute a Delta across two different measurements (a rotated
    secret, a re-pinned arena, or a retired seed all drop out here).

    Reads the isolated ``verified_baseline_atoms`` table, so the floor is
    structurally incapable of appearing in ``read_verified``'s participant
    aggregate and vice versa."""
    return _aggregate(
        store.iter_verified_baseline_atoms(
            secret_fingerprint=secret_fingerprint, evaluator_image=evaluator_image
        ),
        seeds,
    )


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
