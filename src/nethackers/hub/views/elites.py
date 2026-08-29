"""Hub elites view (Part 2 of the hub API redesign): the collectively-best-
per-identity leaderboard, computed LIVE over ``atoms`` on every read -- no
materialized table, no recompute step. This replaces the M2a ``elite_pool``
design (task-8-context.md's full-DELETE-and-rebuild ``recompute_elites``):
that table and its recompute call are gone outright -- a write to ``atoms``
(via ``register``) is visible on the very next read, and there is nothing
to go stale or to forget to recompute.

Ranking, per identity: mean progression (``AVG``) across every atom on that
identity at the given ``tier`` desc; ties broken by total ascensions desc,
then earliest atom (``created_at``) first -- the exact ranking the old
``recompute_elites`` used, now expressed as one windowed SQL query
(``ROW_NUMBER() OVER (PARTITION BY identity ORDER BY ...)``) instead of a
DELETE+rebuild.

``read_elites`` resolves ``scope`` via ``views.boards.resolve_scope`` --
``"generalist"`` is all 73 identities (the "Universe" regime -- see the
redesign's execution ruling), a role/facet/identity narrows to that subset
-- then returns each identity's top-``k`` rows *rank-major*
(``ORDER BY rank, identity``: every identity's rank-1 first,
identity-sorted, then every rank-2, ...), never identity-major and never a
single global top-k -- the deliberate anti-monoculture choice
(task-8-context.md): one identity's elites must never crowd out every
other identity's.

Each row is ``{rank, identity, program_id, owner, score}`` -- narrower than
the old ``elite_pool`` entries, which also carried ``repo``/``commit_sha``/
``tier`` for ``harness/select.py``'s trust-aware cold-start SELECT
(``per_identity_elites``). That consumer is NOT in this redesign's file list
and still reads those fields directly (some via ``entry["solution_digest"]``,
a hard ``KeyError`` on this row shape) -- see the Task 2 report for the
concern raised about it; nothing here papers over that gap.
"""

from __future__ import annotations

from typing import Any

from nethackers.hub.ids import program_id
from nethackers.hub.store import Store
from nethackers.hub.views.boards import resolve_scope

# Per identity: mean progression (score) across every atom at ``tier``, total
# ascensions, and the earliest atom -- ranked score desc, ascensions desc,
# earliest asc, all in one windowed query (no materialized table, nothing to
# recompute). Capped to the top ``k`` per identity, emitted rank-major (every
# identity's rank-1 first, identity-sorted; then every rank-2; ...) so one
# identity's elites can never crowd out another's.
_LIVE_ELITES_SQL = """
WITH agg AS (
  SELECT identity, solution_digest,
         AVG(progression) AS score, SUM(ascended) AS ascensions, MIN(created_at) AS first_at
  FROM atoms WHERE tier = ? AND identity IN ({placeholders})
  GROUP BY identity, solution_digest
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY identity ORDER BY score DESC, ascensions DESC, first_at ASC
  ) AS rank FROM agg
)
SELECT r.identity, r.solution_digest, r.score, r.rank, s.owner
FROM ranked r LEFT JOIN solutions s ON r.solution_digest = s.digest
WHERE r.rank <= ? ORDER BY r.rank, r.identity
"""


def read_elites(
    store: Store, *, scope: str, tier: str = "self-reported", k: int = 8
) -> list[dict[str, Any]]:
    """Top-``k`` solutions per identity in ``scope``'s identity set, computed
    live over ``atoms`` -- always current, nothing stored or recomputed.
    Raises ``ValueError`` for an unknown ``scope`` (``resolve_scope`` --
    mapping that to a 404 is the caller's job, same as ``/board``).

    Each row: ``{rank, identity, program_id, owner, score}``. ``owner``
    comes from a ``LEFT JOIN`` onto the registered ``solutions`` row (the FK
    ``insert_atoms`` enforces means this always resolves in practice), and
    ``program_id`` (``nethackers.hub.ids.program_id``) replaces the old
    ``solution_digest``.
    """
    _kind, ids = resolve_scope(scope)
    placeholders = ", ".join(["?"] * len(ids))
    sql = _LIVE_ELITES_SQL.format(placeholders=placeholders)
    rows = store.conn.execute(sql, (tier, *ids, k)).fetchall()
    return [
        {
            "rank": row[3],
            "identity": row[0],
            "program_id": program_id(row[1]),
            "owner": row[4],
            "score": row[2],
        }
        for row in rows
    ]
