"""Hub boards view (M2a Task 9): the third derived view -- rankings computed
PURELY on read, nothing stored. A board is scored on its objective's
*identity*: ``store.iter_atoms(identity=objective.characters()[0],
tier=tier)``. See task-9-context.md -- the crux was its original RESOLUTION
over the brief's wording ("filter atoms to the objective's characters"):
filtering by character would lump in atoms produced under OTHER objectives
(different published seeds) on the same identity, so two solutions could be
ranked on different atom-sets -- breaking the spec Sec2 comparability
guarantee ("everyone evaluates on the same atoms"). That was originally
solved with a digest filter (each objective scored only on atoms under its
own ``objective_digest``); Task A1 then retired ``random``/``all``, so that
concern no longer applies -- every identity now has exactly one canonical
objective, meaning an identity's atoms are only ever on its own batch.
Filtering by *identity* is therefore equivalent to the old digest filter,
but structural rather than a digest lookup -- Task A2 rekeyed the views
accordingly.

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
from nethackers.hub.ids import program_id
from nethackers.hub.objectives import ALIGNMENTS, GENDERS, IDENTITIES, RACES, ROLES
from nethackers.hub.store import Store
from nethackers.hub.views.milestones import deepest_milestone

_IDENTITY_SET = frozenset(IDENTITIES)

# Facet scopes: each identity is "role-race-align-gender"; a facet scope
# "<facet>:<value>" selects every identity whose facet segment equals value.
_FACET_INDEX = {"role": 0, "race": 1, "align": 2, "gender": 3}
_FACET_VOCAB = {"role": ROLES, "race": RACES, "align": ALIGNMENTS, "gender": GENDERS}


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
    if ":" in token:
        facet, _, value = token.partition(":")
        if facet in _FACET_INDEX and value in _FACET_VOCAB[facet]:
            idx = _FACET_INDEX[facet]
            return (facet, tuple(i for i in IDENTITIES if i.split("-")[idx] == value))
        raise ValueError(f"unknown facet scope: {token!r}")
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


def _finalize_row(store: Store, rank: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Turn one aggregated (pre-rank) entry -- still keyed by the internal
    ``solution_digest`` working key -- into the one uniform ``/board`` row:
    ``{rank, program_id, owner, reference, coverage, identities_total,
    ascensions, mean_progression, median_progression, deepest}``.
    ``reference`` ``{repo, commit}`` comes from ``solutions`` (every atom's
    ``solution_digest`` FKs there, so ``get_solution`` always resolves)."""
    digest = entry.pop("solution_digest")
    solution = store.get_solution(digest) or {}
    reference = {"repo": solution.get("repo", ""), "commit": solution.get("commit_sha", "")}
    return {"rank": rank, "program_id": program_id(digest), "reference": reference, **entry}


def board(
    store: Store, objective: ObjectiveSpec, *, tier: str = "self-reported"
) -> list[dict[str, Any]]:
    """Rank solutions on ``objective``'s *identity* at ``tier``, purely on
    read.

    Filtering: scored on every atom on ``objective.characters()[0]``
    (``random``/``all`` are retired, so each identity has exactly one
    canonical objective -- see module docstring).

    Atoms are grouped by ``solution_digest`` and aggregated in Python:
    ``owner`` (constant per solution -- any atom's), ``ascensions`` (count
    with ``ascended`` True), ``median_progression``/``mean_progression``
    (``statistics.median``/``.mean`` over each atom's ``progression``).
    Ranked per ``objective.aggregation`` (``"asc_median_mean"`` or
    ``"mean"`` -- unknown aggregation raises ``ValueError``). Emits the one
    uniform ``/board`` row (see ``_finalize_row``); an identity is a scope
    of exactly one, so ``coverage``/``identities_total`` are always ``1``.
    ``[]`` when no atoms match.
    """
    sort_key = _SORT_KEYS.get(objective.aggregation)
    if sort_key is None:
        raise ValueError(f"unknown board aggregation: {objective.aggregation!r}")

    atoms = store.iter_atoms(identity=objective.characters()[0], tier=tier)

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
                "coverage": 1,
                "identities_total": 1,
                "ascensions": sum(1 for a in group if a.ascended),
                "median_progression": statistics.median(progressions),
                "mean_progression": statistics.mean(progressions),
                "deepest": deepest_milestone([a.milestone for a in group]),
            }
        )

    unranked.sort(key=sort_key)
    return [_finalize_row(store, rank, entry) for rank, entry in enumerate(unranked, start=1)]


def aggregate_board(
    store: Store, ids: Sequence[str], *, tier: str = "self-reported"
) -> list[dict[str, Any]]:
    """Macro-average board over ``ids`` (generalist = all 73; a role = its
    identities). Each identity is scored on ``iter_atoms(identity=ident)``,
    preserving same-seeds comparability per component (every atom for an
    identity sits on that identity's canonical batch); a solution's
    per-identity means are then rolled up:
      ``coverage`` = #identities in ``ids`` it has >=1 atom on,
      ``identities_total`` = ``len(ids)``,
      ``mean_progression``/``median_progression`` = mean/median of its
      per-identity means over covered ids.
    Ranked coverage desc, mean desc, ``solution_digest`` asc. Emits the same
    uniform ``/board`` row as ``board()`` (see ``_finalize_row``). Pure read."""
    per_solution: dict[str, dict[str, Any]] = {}
    for ident in ids:
        by_sol: dict[str, list[Atom]] = {}
        for atom in store.iter_atoms(identity=ident, tier=tier):
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
            "identities_total": len(ids),
            "mean_progression": statistics.mean(e["means"]),
            "median_progression": statistics.median(e["means"]),
            "ascensions": e["asc"],
            "deepest": deepest_milestone(e["milestones"]),
        }
        for sol, e in per_solution.items()
    ]
    unranked.sort(key=lambda x: (-x["coverage"], -x["mean_progression"], x["solution_digest"]))
    return [_finalize_row(store, rank, entry) for rank, entry in enumerate(unranked, start=1)]


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
