"""Hub Hackers view: a person's standing as the union of all their programs'
best-per-identity over a scope (generalist / role / identity). Pure read;
per-component comparability preserved by scoring each identity on
``iter_atoms(identity=ident)``, exactly like boards.aggregate_board."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from nethackers.hub.objectives import ALIGNMENTS, GENDERS, RACES, ROLES
from nethackers.hub.store import Store
from nethackers.hub.views.boards import resolve_scope


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
            "identities_total": len(ids),
            "mean_progression": statistics.mean(per_ident.values()),
        }
        for owner, per_ident in best.items()
    ]
    unranked.sort(key=lambda x: (-x["coverage"], -x["mean_progression"], x["owner"]))
    return [{"rank": rank, **entry} for rank, entry in enumerate(unranked, start=1)]


_FACET_VALUES = {"role": ROLES, "race": RACES, "align": ALIGNMENTS, "gender": GENDERS}


def leaders(store: Store, by: str, *, tier: str = "self-reported") -> list[dict[str, Any]]:
    """Best hacker for each value of facet ``by`` (role/race/align/gender):
    for each value, the rank-1 owner of the facet-scoped hacker board. Values
    with no scored hackers are omitted. Raises ValueError on an unknown facet."""
    if by not in _FACET_VALUES:
        raise ValueError(f"unknown facet: {by!r}")
    rows: list[dict[str, Any]] = []
    for value in _FACET_VALUES[by]:
        _kind, ids = resolve_scope(f"{by}:{value}")
        board = hacker_board(store, ids, tier=tier)
        if board:
            top = board[0]
            rows.append({"value": value, "owner": top["owner"],
                         "score": top["mean_progression"], "coverage": top["coverage"]})
    return rows
