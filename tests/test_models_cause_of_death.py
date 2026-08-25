from nethackers.contracts.models import TrajectoryResult


def _result(**over):
    base = dict(
        trajectory_id=1, status="completed", progress=0.5, ascended=False,
        steps=10, turns=83, max_depth=1, end_status="1", error=None,
        wall_seconds=1.0, character="val-dwa-law-fem", milestone=None,
    )
    base.update(over)
    return TrajectoryResult(**base)


def test_cause_of_death_defaults_to_none():
    assert _result().cause_of_death is None


def test_cause_of_death_round_trips():
    r = _result(cause_of_death="killed by a jackal")
    assert r.to_dict()["cause_of_death"] == "killed by a jackal"
    assert TrajectoryResult.from_dict(r.to_dict()).cause_of_death == "killed by a jackal"


def test_from_dict_tolerates_legacy_dict_without_field():
    d = _result(cause_of_death="killed by a newt").to_dict()
    del d["cause_of_death"]  # a pre-feature results.json line
    assert TrajectoryResult.from_dict(d).cause_of_death is None
