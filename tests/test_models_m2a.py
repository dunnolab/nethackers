"""M2a additions to ``nethackers.contracts.models``: ``ObjectiveSpec`` (a
grading functional over a published ``(seed, character)`` batch), ``Atom``
(one episode's stored, immutable result), and ``TrajectoryResult`` gaining
per-episode ``character``/``milestone`` -- defaulted so M1's existing
positional/keyword construction sites and ``from_dict(old_dict)`` still work
unchanged (Task 2 will populate the new fields for real)."""

from nethackers.contracts.models import Atom, Objective, TrajectoryResult, end_status_word


def test_end_status_word_translates_nle_codes_and_passes_words_through():
    # arena stores the raw NLE StepStatus code as a string; the shared helper
    # both the monitor and the mutator brief use maps it to a human word.
    assert end_status_word("1") == "died"
    assert end_status_word("-1") == "aborted"
    assert end_status_word("0") == "running"
    assert end_status_word(1) == "died"              # tolerant of an int code
    assert end_status_word(None) is None             # no outcome recorded
    assert end_status_word("") is None
    assert end_status_word("died") == "died"         # already a word -> passthrough
    assert end_status_word("7") == "7"               # unknown code -> as-is, never crash


def test_objective_action_timeout_default_is_the_local_hang_guard():
    # Root cause ③, done right: the per-action wall-clock budget is a HANG-GUARD
    # (NLE/AutoAscend can hang), not a scoring knob. It defaults to a generous
    # 120s so a normal action -- including a first-action cold numba JIT compile
    # under parallel load -- is never cut; only a genuinely hung bot times out.
    # (Was 5.0, which cut normal AutoAscend actions under contention and
    # corrupted the hub baseline.) The held-out validator sets its own value.
    assert Objective(character="val-dwa-law-fem").action_timeout_seconds == 120.0


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
