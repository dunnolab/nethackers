"""Per-solution frontier view: one solution's mean progression per identity
(the Program regime of the Frontier). Unlike attainment (community-wide,
first-to-reach) this is scoped to a single solution_digest."""
from __future__ import annotations

from typing import Any

from nethackers.hub.store import Store

_FRONTIER_SQL = """
SELECT identity, AVG(progression) AS progression, COUNT(*) AS episodes
FROM atoms
WHERE solution_digest = ?
GROUP BY identity
ORDER BY identity
"""


def read_solution_frontier(store: Store, solution_digest: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(_FRONTIER_SQL, (solution_digest,)).fetchall()
    return [
        {"identity": str(r[0]), "progression": float(r[1]), "episodes": int(r[2])}
        for r in rows
    ]
