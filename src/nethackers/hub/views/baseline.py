"""AutoAscend reference floor: per-identity aggregates (mean progression,
deepest milestone reached, episode count) over ``baseline_atoms`` for the
self-reported tier and ``verified_baseline_atoms`` for the verified tier.
Same milestone ordering (ACHIEVEMENTS) the attainment view uses."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.milestones import deepest_milestone
from nethackers.hub.views.source import Epoch, source_for


def per_identity_fold(atoms: Iterable[Atom]) -> dict[str, Any]:
    """Fold atoms into ``{per_identity, overall}``. ``overall`` is the mean OF
    THE PER-IDENTITY MEANS, so an identity with more episodes recorded doesn't
    weigh more heavily than the rest.

    ``overall`` is ``None``, never ``0.0``, when nothing has been measured --
    "not computed yet" and "scored zero" are different claims, and conflating
    them would show every program beating an uncomputed floor.

    Shared with ``views.verified``: the verified aggregate is this same fold
    over a different table, and having it written twice is how the two drift.
    """
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
        overall = round(
            statistics.mean(c["progression"] for c in per_identity.values()), 3
        )
    return {"per_identity": per_identity, "overall": overall}


def read_baseline(
    store: Store, *, tier: str = "self-reported", epoch: Epoch | None = None
) -> dict[str, Any]:
    """AutoAscend's floor for ``tier`` -- ``baseline_atoms`` for the
    self-reported tier, ``verified_baseline_atoms`` (epoch-scoped) for the
    verified one. The source pairs the floor to the tier, so a hidden-seed
    board can never be handed the published-seed floor.

    An unrecognized ``tier`` falls through ``source_for`` to ``epoch=None``
    and so reads the same published ``baseline_atoms`` floor as
    "self-reported", rather than the zero rows an unknown tier gets on the
    participant-atoms views. This is a deliberate ruling, not a gap: baseline
    rows carry no participant ``tier`` column to filter on (``iter_baseline_atoms``
    never applies one -- see ``views.source``), so there is nothing to make
    empty. Failing open to the existing public floor risks no epoch violation
    and no seed leak, so special-casing a value nothing sends would be
    speculative complexity."""
    source = source_for(tier, epoch)
    return {"owner": "autoascend", **per_identity_fold(source.iter_baseline_atoms(store))}
