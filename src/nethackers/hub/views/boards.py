"""Hub boards view (M2a Task 9): the third derived view -- rankings computed
PURELY on read, nothing stored. See task-9-context.md -- the crux is its
RESOLUTION over the brief's wording ("filter atoms to the objective's
characters"): filtering by character would lump in atoms produced under
OTHER objectives (different published seeds) on the same identity, so two
solutions could be ranked on different atom-sets -- breaking the spec Sec2
comparability guarantee ("everyone evaluates on the same atoms"). Instead:

- Every objective is scored only on atoms produced under exactly its own
  objective: ``store.iter_atoms(objective_digest=objective.digest(),
  tier=tier)``. Same batch => fair, same-atom-set ranking. (The prior
  functional ``"all"`` breadth rollup -- every atom at a tier, regardless of
  which objective produced it -- was retired alongside ``random``/``all``:
  see ``selector.py``'s module docstring.)

Aggregation happens in Python, not SQL: sqlite has no ``MEDIAN()``, and
``asc_median_mean`` needs one, so atoms are pulled via ``iter_atoms`` and
reduced with ``statistics.median``/``statistics.mean`` per solution.
``coverage_board``/``firsts_board`` are the exception -- they read the
attainment record (Task 7's ``attainment``/``attainment_holders`` tables),
which sqlite's own ``COUNT``/``GROUP BY`` handles directly.

Nothing in this module writes anything: no new tables, no mutation of
``atoms``/``attainment``/``attainment_holders``/``elite_pool``. Pure reads
over what Tasks 5-8 already store.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from typing import Any

from nethackers.contracts.models import Atom, ObjectiveSpec
from nethackers.hub.objectives import CATALOG, IDENTITIES, ROLES
from nethackers.hub.store import Store
from nethackers.hub.views.milestones import deepest_milestone

_IDENTITY_SET = frozenset(IDENTITIES)


def resolve_scope(token: str) -> tuple[str, tuple[str, ...]]:
    """Map an objective token to ``(kind, ids)``. ``"generalist"`` -> all 73;
    a role (e.g. ``"val"``) -> that role's identities; an identity -> a
    singleton. Raises ``ValueError`` on anything else. Distinct from
    ``selector.resolve`` (which serves the evolve path's glob/comma/'all')."""
    if token == "generalist":
        return ("generalist", tuple(IDENTITIES))
    if token in ROLES:
        return ("role", tuple(i for i in IDENTITIES if i.startswith(f"{token}-")))
    if token in _IDENTITY_SET:
        return ("identity", (token,))
    raise ValueError(f"unknown objective scope: {token!r}")

# board()'s two ranking rules (task-9-context.md), keyed by
# ObjectiveSpec.aggregation. Each maps one aggregated entry dict to a sort
# key tuple; Python's sort is stable and tuples compare lexicographically,
# so these ARE the ranking rules, verbatim:
#   - "asc_median_mean": ascensions desc, then median desc, then mean desc,
#     then solution_digest asc as the final deterministic tiebreak.
#   - "mean": mean desc, then solution_digest asc.
# (Every key negates the numeric fields so ascending tuple sort yields
# descending rank order; solution_digest sorts ascending un-negated.)
_SORT_KEYS: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]] = {
    "asc_median_mean": lambda e: (
        -e["ascensions"],
        -e["median_progression"],
        -e["mean_progression"],
        e["solution_digest"],
    ),
    "mean": lambda e: (-e["mean_progression"], e["solution_digest"]),
}

# Cells held per solution: attainment_holders' PK (identity, milestone,
# solution_digest) already dedups multiple seeds/episodes of one solution
# down to one row per cell, so COUNT(*) is exactly the distinct-cells count.
_COVERAGE_SQL = """
SELECT solution_digest, MIN(owner) AS owner, COUNT(*) AS cells_held
FROM attainment_holders
GROUP BY solution_digest
ORDER BY cells_held DESC, solution_digest ASC
"""

# Cells a solution was FIRST to reach: attainment has exactly one row per
# (identity, milestone), holding whichever solution's first_at ratcheted
# earliest (Task 7) -- so grouping by first_solution and counting rows is
# exactly "how many cells was this solution first to light".
_FIRSTS_SQL = """
SELECT first_solution AS solution_digest, MIN(first_owner) AS owner, COUNT(*) AS firsts
FROM attainment
GROUP BY first_solution
ORDER BY firsts DESC, first_solution ASC
"""


def board(
    store: Store, objective: ObjectiveSpec, *, tier: str = "self-reported"
) -> list[dict[str, Any]]:
    """Rank solutions on ``objective`` at ``tier``, purely on read.

    Filtering (task-9-context.md's RESOLUTION -- by digest, not character):
    scored only on atoms produced under exactly ``objective.digest()`` (the
    prior functional ``"all"`` breadth-rollup branch was retired alongside
    ``random``/``all`` -- see module docstring).

    Atoms are grouped by ``solution_digest`` and aggregated in Python:
    ``owner`` (constant per solution -- any atom's), ``episodes`` (atom
    count), ``ascensions`` (count with ``ascended`` True),
    ``median_progression``/``mean_progression`` (``statistics.median``/
    ``.mean`` over each atom's ``progression``). Ranked per
    ``objective.aggregation`` (``"asc_median_mean"`` or ``"mean"`` --
    unknown aggregation raises ``ValueError``), each entry gets ``rank``
    1..n in ranked order: ``{rank, solution_digest, owner, episodes,
    ascensions, median_progression, mean_progression}``. ``[]`` when no
    atoms match.
    """
    sort_key = _SORT_KEYS.get(objective.aggregation)
    if sort_key is None:
        raise ValueError(f"unknown board aggregation: {objective.aggregation!r}")

    atoms = store.iter_atoms(objective_digest=objective.digest(), tier=tier)

    grouped: dict[str, list[Atom]] = {}
    for atom in atoms:
        grouped.setdefault(atom.solution_digest, []).append(atom)

    unranked: list[dict[str, Any]] = []
    for solution_digest, group in grouped.items():
        progressions = [a.progression for a in group]
        unranked.append(
            {
                "solution_digest": solution_digest,
                "owner": group[0].owner,
                "episodes": len(group),
                "ascensions": sum(1 for a in group if a.ascended),
                "median_progression": statistics.median(progressions),
                "mean_progression": statistics.mean(progressions),
                "deepest": deepest_milestone([a.milestone for a in group]),
            }
        )

    unranked.sort(key=sort_key)
    return [{"rank": rank, **entry} for rank, entry in enumerate(unranked, start=1)]


def aggregate_board(
    store: Store, ids: Sequence[str], *, tier: str = "self-reported"
) -> list[dict[str, Any]]:
    """Macro-average board over ``ids`` (generalist = all 73; a role = its
    identities). Each identity is scored on ITS OWN published batch
    (``objective_digest``), preserving same-seeds comparability per component;
    a solution's per-identity means are then rolled up:
      ``coverage`` = #identities in ``ids`` it has >=1 atom on,
      ``mean_progression`` = mean of its per-identity means over covered ids.
    Ranked coverage desc, mean desc, ``solution_digest`` asc. Pure read."""
    per_solution: dict[str, dict[str, Any]] = {}
    for ident in ids:
        digest = CATALOG[ident].digest()
        by_sol: dict[str, list[Atom]] = {}
        for atom in store.iter_atoms(objective_digest=digest, tier=tier):
            by_sol.setdefault(atom.solution_digest, []).append(atom)
        for sol, group in by_sol.items():
            entry = per_solution.setdefault(
                sol, {"owner": group[0].owner, "means": [], "asc": 0, "milestones": []}
            )
            entry["means"].append(statistics.mean(a.progression for a in group))
            entry["asc"] += sum(1 for a in group if a.ascended)
            entry["milestones"].extend(a.milestone for a in group)

    unranked = [
        {
            "solution_digest": sol,
            "owner": e["owner"],
            "coverage": len(e["means"]),
            "total": len(ids),
            "mean_progression": statistics.mean(e["means"]),
            "ascensions": e["asc"],
            "deepest": deepest_milestone(e["milestones"]),
        }
        for sol, e in per_solution.items()
    ]
    unranked.sort(key=lambda x: (-x["coverage"], -x["mean_progression"], x["solution_digest"]))
    return [{"rank": rank, **entry} for rank, entry in enumerate(unranked, start=1)]


def coverage_board(store: Store) -> list[dict[str, Any]]:
    """Every solution that holds at least one attainment cell, each as
    ``{rank, solution_digest, owner, cells_held}`` -- ``cells_held`` is the
    count of distinct ``(identity, milestone)`` cells it holds
    (``attainment_holders``), ranked ``cells_held`` desc then
    ``solution_digest`` asc (the deterministic tiebreak). ``[]`` when
    nothing is lit yet.
    """
    rows = store.conn.execute(_COVERAGE_SQL).fetchall()
    return [
        {"rank": rank, "solution_digest": row[0], "owner": row[1], "cells_held": row[2]}
        for rank, row in enumerate(rows, start=1)
    ]


def firsts_board(store: Store) -> list[dict[str, Any]]:
    """Every solution that was first to reach at least one attainment cell,
    each as ``{rank, solution_digest, owner, firsts}`` -- ``firsts`` is the
    count of cells it was the earliest holder of (``attainment``), ranked
    ``firsts`` desc then ``solution_digest`` asc (the deterministic
    tiebreak). ``[]`` when nothing is lit yet.
    """
    rows = store.conn.execute(_FIRSTS_SQL).fetchall()
    return [
        {"rank": rank, "solution_digest": row[0], "owner": row[1], "firsts": row[2]}
        for rank, row in enumerate(rows, start=1)
    ]
