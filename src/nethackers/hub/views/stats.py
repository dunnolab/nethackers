"""Headline counters for the website sidebar: pure aggregate reads."""

from __future__ import annotations

from typing import Any

from nethackers.hub.store import Store

_COUNT_PROGRAMS_SQL = "SELECT COUNT(*) FROM solutions"
_COUNT_HACKERS_SQL = "SELECT COUNT(DISTINCT owner) FROM solutions"
_COUNT_ASCENSIONS_SQL = "SELECT COUNT(*) FROM atoms WHERE ascended = 1"
_COUNT_IDENTITIES_SQL = "SELECT COUNT(DISTINCT identity) FROM atoms"
_BEST_PROGRESSION_SQL = "SELECT MAX(progression) FROM atoms"


def read_stats(store: Store) -> dict[str, Any]:
    programs = store.conn.execute(_COUNT_PROGRAMS_SQL).fetchone()[0]
    hackers = store.conn.execute(_COUNT_HACKERS_SQL).fetchone()[0]
    ascensions = store.conn.execute(_COUNT_ASCENSIONS_SQL).fetchone()[0]
    identities = store.conn.execute(_COUNT_IDENTITIES_SQL).fetchone()[0]
    best = store.conn.execute(_BEST_PROGRESSION_SQL).fetchone()[0]
    return {"programs": int(programs), "hackers": int(hackers), "ascensions": int(ascensions),
            "identities_touched": int(identities), "best": float(best) if best is not None else 0.0}
