"""Hub Hackers view: a person's standing as the union of all their programs'
best-per-identity over a scope (generalist / role / identity). Pure read;
per-component comparability preserved by scoring each identity on
``iter_atoms(identity=ident)``, exactly like boards.aggregate_board."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from nethackers.hub.store import Store


def hacker_board(
    store: Store, ids: Sequence[str], *, tier: str = "self-reported"
) -> list[dict[str, Any]]:
    """Rank owners by the union of their programs' best-per-identity over
    ``ids``. For each identity (scored on ``iter_atoms(identity=ident)``),
    take each owner's best mean across that owner's solutions; then per
    owner:
      ``coverage`` = #identities in ``ids`` the owner has touched,
      ``mean_progression`` = mean of the owner's best-per-identity over them.
    Ranked coverage desc, mean desc, ``owner`` asc. Pure read; no ``firsts``."""
    best: dict[str, dict[str, float]] = {}  # owner -> {identity -> best mean}
    for ident in ids:
        by_os: dict[tuple[str, str], list[float]] = {}
        for atom in store.iter_atoms(identity=ident, tier=tier):
            by_os.setdefault((atom.owner, atom.solution_digest), []).append(atom.progression)
        for (owner, _sol), progs in by_os.items():
            mean = statistics.mean(progs)
            slot = best.setdefault(owner, {})
            if ident not in slot or mean > slot[ident]:
                slot[ident] = mean

    unranked: list[dict[str, Any]] = [
        {
            "owner": owner,
            "coverage": len(per_ident),
            "total": len(ids),
            "mean_progression": statistics.mean(per_ident.values()),
        }
        for owner, per_ident in best.items()
    ]
    unranked.sort(key=lambda x: (-x["coverage"], -x["mean_progression"], x["owner"]))
    return [{"rank": rank, **entry} for rank, entry in enumerate(unranked, start=1)]
