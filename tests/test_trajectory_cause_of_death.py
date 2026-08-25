from nethackers.arena.environment import EnvironmentMetrics
from nethackers.arena.trajectory import _result


def _metrics(cause):
    return EnvironmentMetrics(
        progress=0.5, turns=83, max_depth=1, ascended=False,
        end_status="1", milestone=None, cause_of_death=cause,
    )


def test_result_threads_cause_of_death_on_completed():
    r = _result(1, "completed", _metrics("killed by a jackal"), 10, 0.0, "val-dwa-law-fem")
    assert r.cause_of_death == "killed by a jackal"


def test_bot_failure_zeroes_cause_of_death():
    r = _result(1, "bot_error", _metrics("killed by a jackal"), 10, 0.0, "val", error="boom")
    assert r.cause_of_death is None
