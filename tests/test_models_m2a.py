"""M2a additions to ``nethackers.contracts.models``: ``ObjectiveSpec`` (a
grading functional over a published ``(seed, character)`` batch), ``Atom``
(one episode's stored, immutable result), and ``TrajectoryResult`` gaining
per-episode ``character``/``milestone`` -- defaulted so M1's existing
positional/keyword construction sites and ``from_dict(old_dict)`` still work
unchanged (Task 2 will populate the new fields for real)."""

from nethackers.contracts.models import Atom, ObjectiveSpec, TrajectoryResult


def test_objectivespec_digest_depends_on_batch_and_aggregation():
    a = ObjectiveSpec(
        name="random",
        kind="distribution",
        batch=((1, "val-dwa-law-fem"), (2, "wiz-elf-cha-fem")),
        max_steps=1000,
        no_progress_timeout=100,
        action_timeout_seconds=5.0,
        aggregation="asc_median_mean",
    )
    diff_batch = ObjectiveSpec(
        name="random",
        kind="distribution",
        batch=((1, "val-dwa-law-fem"), (2, "val-dwa-law-fem")),
        max_steps=1000,
        no_progress_timeout=100,
        action_timeout_seconds=5.0,
        aggregation="asc_median_mean",
    )
    diff_aggregation = ObjectiveSpec(
        name="random",
        kind="distribution",
        batch=((1, "val-dwa-law-fem"), (2, "wiz-elf-cha-fem")),
        max_steps=1000,
        no_progress_timeout=100,
        action_timeout_seconds=5.0,
        aggregation="mean",
    )

    assert a.digest() == a.digest()
    assert a.digest() != diff_batch.digest()
    assert a.digest() != diff_aggregation.digest()
    assert a.characters() == ("val-dwa-law-fem", "wiz-elf-cha-fem")


def test_trajectoryresult_roundtrips_with_character_and_milestone():
    r = TrajectoryResult(
        trajectory_id=0,
        status="completed",
        progress=0.3,
        ascended=False,
        steps=10,
        turns=5,
        max_depth=3,
        end_status="died",
        error=None,
        wall_seconds=0.1,
        character="val-dwa-law-fem",
        milestone="Dlvl:3",
    )
    assert TrajectoryResult.from_dict(r.to_dict()) == r
    assert r.character == "val-dwa-law-fem"
    assert r.milestone == "Dlvl:3"


def test_trajectoryresult_defaults_character_and_milestone_when_omitted():
    # M1 construction sites don't pass character/milestone at all -- the new
    # fields must default so those call sites keep working unchanged.
    r = TrajectoryResult(
        trajectory_id=0,
        status="completed",
        progress=0.3,
        ascended=False,
        steps=10,
        turns=5,
        max_depth=3,
        end_status=None,
        error=None,
        wall_seconds=0.1,
    )
    assert r.character == ""
    assert r.milestone is None


def test_trajectoryresult_from_dict_tolerates_legacy_dicts():
    # e.g. eval/runner.py deserializes results.json written before this
    # task existed -- from_dict(**value) must not KeyError on the missing
    # character/milestone keys.
    legacy_dict = {
        "trajectory_id": 0,
        "status": "completed",
        "progress": 0.3,
        "ascended": False,
        "steps": 10,
        "turns": 5,
        "max_depth": 3,
        "end_status": None,
        "error": None,
        "wall_seconds": 0.1,
    }
    r = TrajectoryResult.from_dict(legacy_dict)
    assert r.character == ""
    assert r.milestone is None


def test_atom_roundtrips():
    at = Atom(
        solution_digest="sha256:s",
        objective_digest="sha256:o",
        owner="sam",
        tier="self-reported",
        identity="val-dwa-law-fem",
        seed=7,
        progression=0.3,
        milestone="Dlvl:3",
        ascended=False,
        status="completed",
        turns=5,
        steps=10,
        evaluator_image="img@sha256:x",
    )
    assert Atom.from_dict(at.to_dict()) == at


def test_atom_allows_milestone_none():
    at = Atom(
        solution_digest="sha256:s",
        objective_digest="sha256:o",
        owner="sam",
        tier="self-reported",
        identity="val-dwa-law-fem",
        seed=7,
        progression=0.0,
        milestone=None,
        ascended=False,
        status="bot_error",
        turns=0,
        steps=0,
        evaluator_image="img@sha256:x",
    )
    assert Atom.from_dict(at.to_dict()) == at
    assert at.milestone is None
