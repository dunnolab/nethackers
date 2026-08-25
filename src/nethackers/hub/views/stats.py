"""Headline counters for the website sidebar: pure aggregate reads."""

from __future__ import annotations

from typing import Any

from nethackers.hub.store import Store


def read_stats(store: Store) -> dict[str, Any]:
    programs = store.conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]
    hackers = store.conn.execute("SELECT COUNT(DISTINCT owner) FROM solutions").fetchone()[0]
    ascensions = store.conn.execute("SELECT COUNT(*) FROM atoms WHERE ascended = 1").fetchone()[0]
    identities = store.conn.execute("SELECT COUNT(DISTINCT identity) FROM atoms").fetchone()[0]
    best = store.conn.execute("SELECT MAX(progression) FROM atoms").fetchone()[0]
    return {"programs": int(programs), "hackers": int(hackers), "ascensions": int(ascensions),
            "identities_touched": int(identities), "best": float(best) if best is not None else 0.0}
