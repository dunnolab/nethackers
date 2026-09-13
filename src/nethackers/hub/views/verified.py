"""Verified tier aggregate read: per-identity aggregates over verified_atoms
(mean progression, deepest milestone reached, episode count). Same milestone
ordering (ACHIEVEMENTS) the attainment view uses. Also provides verification_status
helper to report per-solution verification progress."""

from __future__ import annotations

from typing import Any

from nethackers.arena_version import ARENA_MAJOR
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.baseline import per_identity_fold


def _aggregate(atoms, seeds) -> dict[str, Any]:
    """Fold hidden-seed atoms into ``{per_identity, overall}``, keeping only
    seeds still in the current hidden list. The fold itself is
    ``views.baseline.per_identity_fold`` -- one implementation, two tables."""
    live = frozenset(seeds)
    return per_identity_fold([a for a in atoms if a.seed in live])


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
        arena_major=ARENA_MAJOR,
    )
    if latest is not None:
        return {
            "state": "attempted",
            "done": done,
            "total": total,
            "failure_kind": latest["failure_kind"],
        }
    return {"state": "not_attempted", "done": done, "total": total, "failure_kind": None}
