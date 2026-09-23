"""Verified tier aggregate read: per-identity aggregates over verified_atoms
(mean progression, deepest milestone reached, episode count). Same milestone
ordering (ACHIEVEMENTS) the attainment view uses. Also provides verification_status
helper to report per-solution verification progress."""

from __future__ import annotations

from typing import Any

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
    store: Store, *, secret_fingerprint: str, seeds, arena_major: int
) -> dict[str, Any]:
    """Read verified-tier aggregates filtered to current secret fingerprint,
    arena major, and seed set. Returns per-identity aggregates (progression
    mean, deepest milestone, episode count) and overall progression mean."""
    return _aggregate(
        store.iter_verified_atoms(
            secret_fingerprint=secret_fingerprint, arena_major=arena_major
        ),
        seeds,
    )


def read_verified_baseline(
    store: Store, *, secret_fingerprint: str, seeds, arena_major: int
) -> dict[str, Any]:
    """Read AutoAscend's hidden-seed floor, scoped to the same epoch as
    ``read_verified`` and returned in the same shape -- so a board can put a
    program's verified number next to the floor's without reshaping either,
    and can never compute a Delta across two different measurements (a rotated
    secret, a bumped arena major, or a retired seed all drop out here).

    Reads the isolated ``verified_baseline_atoms`` table, so the floor is
    structurally incapable of appearing in ``read_verified``'s participant
    aggregate and vice versa."""
    return _aggregate(
        store.iter_verified_baseline_atoms(
            secret_fingerprint=secret_fingerprint, arena_major=arena_major
        ),
        seeds,
    )


def _grid_total(seeds) -> int:
    """Cells in one program's hidden grid: every identity on every live seed.
    Shared so the per-program status and the platform-wide count can never
    disagree about what "fully verified" means."""
    return len(IDENTITIES) * len(seeds)


def verification_status(
    store: Store,
    solution_digest: str,
    *,
    secret_fingerprint: str,
    arena_major: int,
    seeds,
) -> dict[str, Any]:
    """Report verification progress for a solution: state machine
    (not_attempted→attempted→verified), done/total counts, and failure_kind."""
    seed_set = frozenset(seeds)
    total = _grid_total(seeds)
    done = len(
        [
            a
            for a in store.iter_verified_atoms(
                solution_digest=solution_digest,
                secret_fingerprint=secret_fingerprint,
                arena_major=arena_major,
            )
            if a.seed in seed_set
        ]
    )
    if done >= total:
        return {"state": "verified", "done": done, "total": total, "failure_kind": None}
    latest = store.latest_verified_attempt(
        solution_digest,
        secret_fingerprint=secret_fingerprint,
        arena_major=arena_major,
    )
    if latest is not None:
        return {
            "state": "attempted",
            "done": done,
            "total": total,
            "failure_kind": latest["failure_kind"],
        }
    return {"state": "not_attempted", "done": done, "total": total, "failure_kind": None}


# One program is verified when every cell of the hidden grid exists for it
# under the current epoch. ``UNIQUE(solution_digest, identity, seed,
# secret_fingerprint, arena_major)`` on ``verified_atoms`` means a cell can
# appear at most once, so within one epoch a scoped ``COUNT(*)`` per program
# IS its cell count -- no ``COUNT(DISTINCT identity || seed)`` needed. The
# ``identity IN`` clause is not redundant with that: it keeps a row on an
# identity the catalog no longer contains from padding a partial grid up to
# the threshold.
#
# Deliberately one aggregate rather than ``verify_candidates``' per-solution
# Python loop: ``/stats`` is read on every page load, and that loop is
# O(programs x atoms).
_COUNT_VERIFIED_PROGRAMS_SQL = """
SELECT COUNT(*) FROM (
    SELECT solution_digest FROM verified_atoms
    WHERE secret_fingerprint = ? AND arena_major = ?
      AND seed IN ({seeds}) AND identity IN ({identities})
    GROUP BY solution_digest
    HAVING COUNT(*) >= ?
)
"""


def count_verified_programs(
    store: Store, *, secret_fingerprint: str, arena_major: int, seeds
) -> int:
    """How many programs are fully verified under this epoch -- every identity
    on every live hidden seed, the same ``done >= total`` bar
    ``verification_status`` reports per program, counted across all of them.

    Scoped, so the number means one thing: a rotated secret, a bumped arena
    major, or a retired seed drops a program back out of the count rather than
    pooling two measurements that were never comparable.

    An epoch with no seeds can verify nothing, so it counts nothing -- also
    keeps a misconfigured hub off a ``seed IN ()`` syntax error.
    """
    if not seeds:
        return 0
    sql = _COUNT_VERIFIED_PROGRAMS_SQL.format(
        seeds=",".join("?" * len(seeds)), identities=",".join("?" * len(IDENTITIES)),
    )
    params = (secret_fingerprint, arena_major, *seeds, *IDENTITIES, _grid_total(seeds))
    return int(store.conn.execute(sql, params).fetchone()[0])
