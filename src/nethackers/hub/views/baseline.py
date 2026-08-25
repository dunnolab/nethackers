"""AutoAscend reference floor: per-identity aggregates over baseline_atoms
(mean progression, deepest milestone reached, episode count). Same milestone
ordering (ACHIEVEMENTS) the attainment view uses."""

from __future__ import annotations

import statistics
from typing import Any

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom
from nethackers.hub.store import Store


def _deepest(milestones: list[str | None]) -> str | None:
    ms = [m for m in milestones if m is not None]
    return max(ms, key=lambda m: ACHIEVEMENTS.get(m, 0.0)) if ms else None


def read_baseline(store: Store) -> dict[str, Any]:
    atoms = store.iter_baseline_atoms()
    per: dict[str, list[Atom]] = {}
    for atom in atoms:
        per.setdefault(atom.identity, []).append(atom)
    per_identity: dict[str, dict[str, Any]] = {
        ident: {
            "progression": round(statistics.mean(a.progression for a in group), 3),
            "deepest": _deepest([a.milestone for a in group]),
            "episodes": len(group),
        }
        for ident, group in per.items()
    }
    overall: float | None = None
    if per_identity:
        progressions = [c["progression"] for c in per_identity.values()]
        overall = round(statistics.mean(progressions), 3)
    return {"owner": "autoascend", "per_identity": per_identity, "overall": overall}
