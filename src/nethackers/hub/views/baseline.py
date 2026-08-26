"""AutoAscend reference floor: per-identity aggregates over baseline_atoms
(mean progression, deepest milestone reached, episode count). Same milestone
ordering (ACHIEVEMENTS) the attainment view uses."""

from __future__ import annotations

import statistics
from typing import Any

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.milestones import deepest_milestone


def read_baseline(store: Store) -> dict[str, Any]:
    atoms = store.iter_baseline_atoms()
    per: dict[str, list[Atom]] = {}
    for atom in atoms:
        per.setdefault(atom.identity, []).append(atom)
    per_identity: dict[str, dict[str, Any]] = {
        ident: {
            "progression": round(statistics.mean(a.progression for a in group), 3),
            "deepest": deepest_milestone([a.milestone for a in group]),
            "episodes": len(group),
        }
        for ident, group in per.items()
    }
    overall: float | None = None
    if per_identity:
        progressions = [c["progression"] for c in per_identity.values()]
        overall = round(statistics.mean(progressions), 3)
    return {"owner": "autoascend", "per_identity": per_identity, "overall": overall}
