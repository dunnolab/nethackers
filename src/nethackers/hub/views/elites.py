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

Parked caveat: atoms from different objectives use different published
seeds, so a cross-objective mean isn't perfectly apples-to-apples -- a
solution evaluated on an easier seed subset could outrank one evaluated on
a harder subset even at equal "true" skill. Acceptable at M2a scale;
restricting the mean to a single canonical objective's seeds is a future
refinement.

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
ORDER BY score DESC, ascensions DESC, earliest ASC
LIMIT ?
"""

_DISTINCT_IDENTITIES_SQL = "SELECT DISTINCT identity FROM atoms"

_INSERT_ELITE_SQL = """
INSERT INTO elite_pool (identity, solution_digest, score, rank)
VALUES (?, ?, ?, ?)
"""

_SELECT_IDENTITY_SQL = """
SELECT identity, solution_digest, score, rank
FROM elite_pool
WHERE identity = ?
ORDER BY rank
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
    ``{identity, solution_digest, score, rank}``.

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
        "SELECT identity, solution_digest, score, rank FROM elite_pool "
        f"WHERE identity IN ({placeholders}) ORDER BY rank, identity"
    )
    rows = store.conn.execute(sql, ids).fetchall()
    return _entries(rows)


def _entries(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {"identity": row[0], "solution_digest": row[1], "score": row[2], "rank": row[3]}
        for row in rows
    ]
