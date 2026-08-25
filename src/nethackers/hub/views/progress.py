"""Best-so-far progress time series for the website chart.

`frontier` is the leaderboard's "best mean" tracked over time: for each day it
is the running maximum, over programs, of a program's mean progression on the
objective (mean over that program's atoms registered up to that day). It never
drops. `ascensions` and `coverage` (distinct identity|milestone cells lit) are
cumulative. Reads created_at directly -- a DB column, not an Atom field -- so
this is raw SQL, not iter_atoms.
"""

from __future__ import annotations

from typing import Any

from nethackers.hub.store import Store

_COLS = ("substr(created_at, 1, 10) AS day, solution_digest, "
         "progression, ascended, identity, milestone")
_ATOMS_SQL = f"SELECT {_COLS} FROM atoms WHERE tier = ? ORDER BY day"
_ATOMS_BY_IDENTITY_SQL = (
    f"SELECT {_COLS} FROM atoms WHERE tier = ? AND identity = ? ORDER BY day"
)


def read_progress(store: Store, *, objective: str | None = None,
                  tier: str = "self-reported") -> dict[str, Any]:
    if objective:
        rows = store.conn.execute(_ATOMS_BY_IDENTITY_SQL, (tier, objective)).fetchall()
    else:
        rows = store.conn.execute(_ATOMS_SQL, (tier,)).fetchall()
    if not rows:
        return {"series": []}

    sums: dict[str, float] = {}   # per-program cumulative progression sum
    counts: dict[str, int] = {}   # per-program cumulative episode count
    cells: set[str] = set()       # distinct identity|milestone cells lit
    ascensions = 0
    best = 0.0
    series: list[dict[str, Any]] = []
    current_day = rows[0][0]

    def flush(day: str) -> None:
        nonlocal best
        top = max(sums[d] / counts[d] for d in sums)
        best = max(best, top)
        series.append({"t": day, "frontier": round(best, 3),
                       "ascensions": ascensions, "coverage": len(cells)})

    for day, digest, progression, ascended, identity, milestone in rows:
        if day != current_day:
            flush(current_day)
            current_day = day
        sums[digest] = sums.get(digest, 0.0) + float(progression)
        counts[digest] = counts.get(digest, 0) + 1
        if ascended:
            ascensions += 1
        if milestone is not None:
            cells.add(f"{identity}|{milestone}")
    flush(current_day)
    return {"series": series}
