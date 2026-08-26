"""Tests for ``nethackers.hub.atoms``: the pure ``Evidence -> list[Atom]``
converter (M2a Task 6). Task A3 dropped ``objective_digest`` end-to-end: an
atom no longer references any published ``ObjectiveSpec`` at all -- it is
keyed by ``identity`` (``result.character``), since after random/all's
retirement (Task A1) every identity has exactly one canonical objective.
"""

from __future__ import annotations

from nethackers.contracts.models import Evidence, Objective, ObjectiveSpec, TrajectoryResult
from nethackers.hub.atoms import evidence_to_atoms

SPEC = ObjectiveSpec(
    name="val-dwa-law-fem",
    kind="identity",
    batch=((0, "val-dwa-law-fem"), (1, "wiz-elf-cha-fem")),
    max_steps=1000,
    no_progress_timeout=100,
    action_timeout_seconds=5.0,
    aggregation="mean",
)


def _result(**overrides):
    fields = dict(
        trajectory_id=0,
        status="completed",
        progress=0.3,
        ascended=False,
        steps=10,
        turns=5,
        max_depth=3,
        end_status=None,
        error=None,
        wall_seconds=1.5,
        character="val-dwa-law-fem",
        milestone="Dlvl:3",
    )
    fields.update(overrides)
    return TrajectoryResult(**fields)


def _evidence(*, results, objective=None, **overrides):
    default_objective = Objective(character=None, seed_set=SPEC.name)
    fields = dict(
        solution_digest="sha256:solution-a",
        objective=objective if objective is not None else default_objective,
        evaluator_image="img@sha256:evaluator",
        results=results,
        created_at="2026-08-09T00:00:00Z",
    )
    fields.update(overrides)
    return Evidence.from_results(**fields)


def test_evidence_to_atoms_maps_every_field_for_two_different_characters():
    # Property 1: two results of different characters -> two atoms, with
    # every field pulled from the matching result/evidence.
    results = [
        _result(
            trajectory_id=10, character="val-dwa-law-fem", milestone="Dlvl:3",
            progress=0.3, ascended=False, status="completed", turns=5, steps=10,
        ),
        _result(
            trajectory_id=11, character="wiz-elf-cha-fem", milestone="Dlvl:5",
            progress=0.6, ascended=True, status="completed", turns=8, steps=20,
        ),
    ]
    evidence = _evidence(results=results)

    atoms = evidence_to_atoms(evidence, owner="sam")

    assert len(atoms) == 2
    first, second = atoms

    assert first.identity == "val-dwa-law-fem"
    assert first.seed == 10
    assert first.milestone == "Dlvl:3"
    assert first.progression == 0.3
    assert first.ascended is False
    assert first.status == "completed"
    assert first.turns == 5
    assert first.steps == 10
    assert first.tier == "self-reported"
    assert first.owner == "sam"
    assert first.solution_digest == evidence.solution_digest
    assert first.evaluator_image == evidence.evaluator_image

    assert second.identity == "wiz-elf-cha-fem"
    assert second.seed == 11
    assert second.milestone == "Dlvl:5"
    assert second.progression == 0.6
    assert second.ascended is True
    assert second.status == "completed"
    assert second.turns == 8
    assert second.steps == 20
    assert second.tier == "self-reported"
    assert second.owner == "sam"
    assert second.solution_digest == evidence.solution_digest
    assert second.evaluator_image == evidence.evaluator_image


# NOTE: test_evidence_to_atoms_objective_digest_is_spec_digest_not_evidence_objective_digest
# was removed here (Task A3): it asserted atom.objective_digest, a field Atom
# no longer has -- superseded by test_atom_has_no_objective_digest_and_dedup_is_identity_keyed
# and test_evidence_to_atoms_drops_spec_and_keys_by_identity below.


def test_evidence_to_atoms_empty_results_returns_empty_list():
    # Property 3.
    evidence = _evidence(results=[])

    assert evidence_to_atoms(evidence, owner="sam") == []


def test_evidence_to_atoms_preserves_result_order():
    # Property 4: one atom per result, order preserved -- assert the
    # identity/seed sequence matches evidence.results' order exactly.
    results = [
        _result(trajectory_id=5, character="arc-hum-law-fem"),
        _result(trajectory_id=2, character="bar-hum-neu-mal"),
        _result(trajectory_id=9, character="wiz-elf-cha-fem"),
    ]
    evidence = _evidence(results=results)

    atoms = evidence_to_atoms(evidence, owner="sam")

    assert [(a.identity, a.seed) for a in atoms] == [
        ("arc-hum-law-fem", 5),
        ("bar-hum-neu-mal", 2),
        ("wiz-elf-cha-fem", 9),
    ]


def test_atom_has_no_objective_digest_and_dedup_is_identity_keyed():
    from dataclasses import fields

    from nethackers.contracts.models import Atom
    assert "objective_digest" not in {f.name for f in fields(Atom)}


def test_evidence_to_atoms_drops_spec_and_keys_by_identity():
    ev = Evidence.from_results(
        solution_digest="sha256:s",
        objective=Objective(character=None, seed_set="val-dwa-law-fem"),
        evaluator_image="img",
        results=[TrajectoryResult(
            trajectory_id=0, status="completed", progress=0.5, ascended=False,
            steps=1, turns=1, max_depth=1, end_status="died", error=None,
            wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)],
        created_at="t")
    atoms = evidence_to_atoms(ev, owner="sam", solution_id="repo@sha")
    assert atoms[0].identity == "val-dwa-law-fem"
    assert not hasattr(atoms[0], "objective_digest")
