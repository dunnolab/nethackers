"""Evidence -> atoms conversion (M2a Task 6): the pure function that turns
one eval run's ``Evidence`` into the flat ``list[Atom]`` the hub store
persists -- one ``Atom`` per ``TrajectoryResult`` in ``evidence.results``,
in order.

``spec`` (the *published* ``ObjectiveSpec``, not ``evidence.objective``) is
required to compute ``objective_digest``: Task 3's ``eval_batch`` sets
``Evidence.objective`` to a synthesized ``Objective`` (character=None, the
batch's step/timeout knobs, ``seed_set=spec.name``) -- a different
dataclass whose digest never equals ``ObjectiveSpec.digest()``. Atoms must
carry ``objective_digest == spec.digest()`` because the atoms table's FK
references ``objectives.objective_digest`` (keyed by ``spec.digest()``, per
``store.objectives_upsert``), and boards/attainment/elite-pool group atoms
by that same published digest (task-6-context.md's Resolution).

Pure function of its arguments only: no validation (Task 9's register
ladder checks the batch matches the objective before calling this) and no
catalog import -- the caller (Task 9) resolves ``spec`` from the catalog
and passes it in.
"""

from __future__ import annotations

from nethackers.contracts.models import Atom, Evidence, ObjectiveSpec


def evidence_to_atoms(
    evidence: Evidence, *, owner: str, spec: ObjectiveSpec, solution_id: str | None = None
) -> list[Atom]:
    """One ``Atom`` per entry in ``evidence.results``, in order (``[]`` for
    empty results). ``objective_digest`` is ``spec.digest()``, computed
    once here and reused for every atom -- see module docstring.

    ``solution_id`` overrides the atom's solution key: the hub identifies a
    solution by its ``repo@commit`` link (not the content digest), so atoms
    are keyed by that id to FK-link the solution row. Defaults to
    ``evidence.solution_digest`` when not given (backward compatible)."""
    objective_digest = spec.digest()
    key = solution_id if solution_id is not None else evidence.solution_digest
    return [
        Atom(
            solution_digest=key,
            objective_digest=objective_digest,
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
