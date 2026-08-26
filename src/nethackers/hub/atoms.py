"""Evidence -> atoms conversion (M2a Task 6): the pure function that turns
one eval run's ``Evidence`` into the flat ``list[Atom]`` the hub store
persists -- one ``Atom`` per ``TrajectoryResult`` in ``evidence.results``,
in order.

Task A3 dropped ``objective_digest`` end-to-end: an atom carries no
objective reference at all -- after random/all are retired (Task A1) every
atom for identity F sits on F's canonical batch, so ``identity`` alone is
the key. No ``spec``/catalog dependency either: pure function of its
arguments only.
"""

from __future__ import annotations

from nethackers.contracts.models import Atom, Evidence


def evidence_to_atoms(
    evidence: Evidence, *, owner: str, solution_id: str | None = None
) -> list[Atom]:
    """One ``Atom`` per entry in ``evidence.results``, in order (``[]`` for
    empty results). Keyed by ``identity`` (``result.character``); no objective
    reference -- after random/all are retired every atom for identity F sits
    on F's canonical batch, so the identity IS the key.

    ``solution_id`` overrides the atom's solution key (the hub identifies a
    solution by its ``repo@commit`` link); defaults to
    ``evidence.solution_digest``."""
    key = solution_id if solution_id is not None else evidence.solution_digest
    return [
        Atom(
            solution_digest=key,
            owner=owner,
            tier=evidence.tier,
            identity=result.character,
            seed=result.trajectory_id,
            progression=result.progress,
            milestone=result.milestone,
            ascended=result.ascended,
            status=result.status,
            turns=result.turns,
            steps=result.steps,
            evaluator_image=evidence.evaluator_image,
        )
        for result in evidence.results
    ]
