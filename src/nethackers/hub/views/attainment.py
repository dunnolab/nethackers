"""Hub attainment view (M2a Task 7): the append-only, monotonic record of
which ``(identity, milestone)`` cells are lit, and by whom first. See
task-7-context.md -- the milestone model is the crux:

    Cell (identity, label) is lit iff some atom on identity has
    progression >= ACHIEVEMENTS[label].

``ACHIEVEMENTS`` (``nethackers.arena.progress``) is a single value scale
shared by every achievement label (Dlvl/Xp/Home/Astral/ascend), and an
atom's ``progression`` is set against that same scale (arena/progress.py).
So "reaching a deep milestone lights all shallower ones" falls straight out
of the value order: an atom's progression is compared against *every*
achievement's threshold, not just the label named in ``atom.milestone``.

Writes are strictly additive -- there is no DELETE anywhere in this module:
- ``attainment_holders`` rows are ``INSERT OR IGNORE``, deduped on
  ``(identity, milestone, solution_digest)`` so multiple seeds/episodes of
  the same solution count as one holder.
- ``attainment`` (the "first" record) only ever ratchets to an *earlier*
  ``reached_at`` via ``ON CONFLICT ... WHERE excluded.first_at <
  attainment.first_at`` -- a later atom can never overwrite an earlier
  first, and re-running with identical atoms+``now`` is a no-op
  (idempotent: the holder insert is ignored, and the first ratchet's WHERE
  is false on an equal timestamp).

``Atom`` carries no timestamp, so "first by timestamp" needs one supplied
by the caller: ``now`` is required and applies to every atom in one call
(Task 9's register ladder passes its registration timestamp; tests pass
distinct ``now`` values per call to exercise ordering).
"""

from __future__ import annotations

from typing import Any

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

# Ascending ladder [(label, value), ...], precomputed once at import time
# (not re-sorted per atom/per call). Iterating it and breaking as soon as a
# threshold exceeds the atom's progression yields exactly the labels with
# ACHIEVEMENTS[label] <= atom.progression.
_LADDER: tuple[tuple[str, float], ...] = tuple(
    sorted(ACHIEVEMENTS.items(), key=lambda item: item[1])
)

_INSERT_HOLDER_SQL = """
INSERT OR IGNORE INTO attainment_holders
    (identity, milestone, solution_digest, owner, reached_at)
VALUES (?, ?, ?, ?, ?)
"""

_RATCHET_FIRST_SQL = """
INSERT INTO attainment (identity, milestone, first_solution, first_owner, first_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(identity, milestone) DO UPDATE SET
    first_solution = excluded.first_solution,
    first_owner    = excluded.first_owner,
    first_at       = excluded.first_at
WHERE excluded.first_at < attainment.first_at
"""

_READ_SQL_TEMPLATE = """
SELECT a.identity, a.milestone, a.first_solution, a.first_owner, a.first_at,
       COUNT(h.solution_digest) AS holder_count
FROM attainment a
LEFT JOIN attainment_holders h USING (identity, milestone)
{where}
GROUP BY a.identity, a.milestone, a.first_solution, a.first_owner, a.first_at
"""


def update_attainment(store: Store, atoms: list[Atom], *, now: str) -> None:
    """Light every ``(atom.identity, label)`` cell for which
    ``ACHIEVEMENTS[label] <= atom.progression``, for each atom in
    ``atoms``. Atoms whose ``milestone is None`` are skipped entirely --
    they carry no progress signal to record (task-7-context.md). ``now`` is
    used as every lit cell's ``reached_at`` / candidate ``first_at`` for
    this call -- all atoms passed to one call share it.

    The whole call runs inside a single ``with store.conn:`` transaction:
    either every write in this call lands, or (on error) none does. Safe
    to re-run with the same ``atoms``/``now`` -- see module docstring.
    """
    with store.conn:
        for atom in atoms:
            if atom.milestone is None:
                continue
            for label, value in _LADDER:
                if value > atom.progression:
                    break
                store.conn.execute(
                    _INSERT_HOLDER_SQL,
                    (atom.identity, label, atom.solution_digest, atom.owner, now),
                )
                store.conn.execute(
                    _RATCHET_FIRST_SQL,
                    (atom.identity, label, atom.solution_digest, atom.owner, now),
                )


def read_attainment(store: Store, *, identity: str | None = None) -> list[dict[str, Any]]:
    """Every lit cell, each as ``{identity, milestone, first_solution,
    first_owner, first_at, holder_count}``, optionally narrowed to one
    ``identity``. Sorted by ``(identity, ACHIEVEMENTS[milestone])`` so
    cells come out in ladder order. ``[]`` when nothing is lit."""
    where = "WHERE a.identity = ?" if identity is not None else ""
    params = (identity,) if identity is not None else ()
    rows = store.conn.execute(_READ_SQL_TEMPLATE.format(where=where), params).fetchall()
    cells = [
        {
            "identity": row[0],
            "milestone": row[1],
            "first_solution": row[2],
            "first_owner": row[3],
            "first_at": row[4],
            "holder_count": row[5],
        }
        for row in rows
    ]
    cells.sort(key=lambda cell: (cell["identity"], ACHIEVEMENTS.get(cell["milestone"], 0.0)))
    return cells
