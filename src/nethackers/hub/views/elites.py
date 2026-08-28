"""Hub elite pool view (M2a Task 8): the displaceable, full-recompute top-k
current-best solutions per identity. See task-8-context.md -- unlike
attainment's monotonic, append-only record (Task 7), ``elite_pool`` is
wholesale DELETEd and rebuilt on every ``recompute_elites`` call: a newly-
better solution simply appears in the recomputed top-k, and whatever now
falls outside it is simply not reinserted -- there is no separate "evict"
step, because the DELETE+rebuild *is* the eviction.

Ranking, per identity: mean progression (``AVG``) across ALL atoms with
that ``identity`` (any objective) desc; ties broken by total ascensions
desc, then earliest atom (``created_at``) first.

Every atom for an identity now sits on that identity's canonical batch
(random/all retired), so ``AVG(progression)`` per identity is a same-seeds
mean -- the former cross-objective caveat no longer applies.

``read_elites`` rolls a functional (``all``) or ``random`` objective up
into a *spread* across identities -- round-robin (``ORDER BY rank,
identity``: every identity's rank-1 first, identity-sorted, then every
rank-2, ...) -- rank-major, never identity-major and never a single global
top-k. This is the deliberate anti-monoculture choice (task-8-context.md):
one identity's elites must never crowd out every other identity's.
"""

from __future__ import annotations

from typing import Any

from nethackers.hub.objectives import CATALOG, IDENTITIES
from nethackers.hub.store import Store

# Per identity, per solution: mean progression (score), total ascensions,
# and the earliest atom -- ranked score desc, then ascensions desc, then
# earliest asc, capped to the top k. task-8-context.md's SQL, verbatim.
_RANK_SQL = """
SELECT solution_digest,
       AVG(progression)  AS score,
       SUM(ascended)      AS ascensions,
       MIN(created_at)    AS earliest
FROM atoms
WHERE identity = ?
GROUP BY solution_digest
ORDER BY score DESC, ascensions DESC, earliest ASC, solution_digest ASC
LIMIT ?
"""

_DISTINCT_IDENTITIES_SQL = "SELECT DISTINCT identity FROM atoms"

_INSERT_ELITE_SQL = """
INSERT INTO elite_pool (identity, solution_digest, score, rank)
VALUES (?, ?, ?, ?)
"""

_TIER = "self-reported"  # every atom is self-reported until M2b verification exists

# LEFT JOIN (not INNER): solution_digest -> solutions.digest always resolves
# in practice (atoms.solution_digest carries an FK to solutions(digest), and
# elite_pool rows are derived from atoms -- see store.py's insert_atoms), but
# LEFT keeps a stray/legacy elite_pool row from vanishing outright if that
# ever weren't true; owner/repo/commit_sha just come back NULL.
_SELECT_IDENTITY_SQL = """
SELECT ep.identity, ep.solution_digest, ep.score, ep.rank,
       s.owner, s.repo, s.commit_sha
FROM elite_pool ep
LEFT JOIN solutions s ON ep.solution_digest = s.digest
WHERE ep.identity = ?
ORDER BY ep.rank
"""


def recompute_elites(store: Store, *, k: int = 8) -> None:
    """Full, atomic recompute of ``elite_pool``: DELETE every row, then for
    each identity with at least one atom (``SELECT DISTINCT identity FROM
    atoms``), re-rank its solutions by mean progression (ties: ascensions
    desc, then earliest atom first) and reinsert its top-``k`` with
    ``rank`` 1..k in ranked order.

    The whole call runs inside a single ``with store.conn:`` transaction:
    either every write lands, or (on error) none does. Idempotent --
    rerunning with unchanged atoms reproduces the same rows. Displaceable
    -- a solution that newly beats the current top-k simply appears in the
    rebuilt ranking; whatever solution now falls outside the top-k is
    simply not reinserted.
    """
    with store.conn:
        store.conn.execute("DELETE FROM elite_pool")
        identities = [
            row[0] for row in store.conn.execute(_DISTINCT_IDENTITIES_SQL).fetchall()
        ]
        for identity in identities:
            ranked = store.conn.execute(_RANK_SQL, (identity, k)).fetchall()
            for rank, row in enumerate(ranked, start=1):
                solution_digest, score = row[0], row[1]
                store.conn.execute(_INSERT_ELITE_SQL, (identity, solution_digest, score, rank))


def read_elites(store: Store, *, objective: str) -> list[dict[str, Any]]:
    """Entries for ``objective`` (a catalog objective name), each
    ``{identity, solution_digest, score, rank, owner, repo, commit_sha,
    tier}`` -- the last four (M3 SELECT-from-hub) come from a ``LEFT JOIN``
    onto the registered ``solutions`` row (``tier`` is always the constant
    ``"self-reported"``; see ``_TIER``) and are exactly what a trust-aware
    SELECT (``harness/select.py``) needs to decide whether an elite is
    trusted.

    - ``spec.kind == "identity"`` (``objective`` IS one of the 73
      identities): that identity's own top-k, in rank order.
    - ``spec.kind == "functional"`` (``"all"``): a spread rollup across
      every catalog identity, ``ORDER BY rank, identity`` -- rank-major
      (every identity's rank-1 first, identity-sorted; then every rank-2;
      ...), never one identity's whole top-k before the next.
    - ``spec.kind == "random"``: the same rank-major spread, narrowed to
      the objective's own sampled identities (``set(spec.characters())``).

    Unknown ``objective`` -- ``CATALOG[objective]`` raises ``KeyError``
    (validating that is the caller's job).
    """
    spec = CATALOG[objective]
    if spec.kind == "identity":
        rows = store.conn.execute(_SELECT_IDENTITY_SQL, (spec.name,)).fetchall()
        return _entries(rows)

    ids = IDENTITIES if spec.kind == "functional" else tuple(sorted(set(spec.characters())))
    placeholders = ", ".join(["?"] * len(ids))
    sql = (
        "SELECT ep.identity, ep.solution_digest, ep.score, ep.rank, "
        "s.owner, s.repo, s.commit_sha "
        "FROM elite_pool ep LEFT JOIN solutions s ON ep.solution_digest = s.digest "
        f"WHERE ep.identity IN ({placeholders}) ORDER BY ep.rank, ep.identity"
    )
    rows = store.conn.execute(sql, ids).fetchall()
    return _entries(rows)


def _entries(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "identity": row[0], "solution_digest": row[1], "score": row[2], "rank": row[3],
            "owner": row[4], "repo": row[5], "commit_sha": row[6], "tier": _TIER,
        }
        for row in rows
    ]
