"""Exercise run_trajectory's M2a additions: threading `character` through to
TrajectoryResult and surfacing EnvironmentMetrics.milestone as
TrajectoryResult.milestone.

Extends tests/test_trajectory.py's FakeEnv/FakeClient pattern (no NLE, no
real bot subprocess) with a `milestone` on the fake EnvironmentMetrics and an
explicit `character` argument to run_trajectory. Covers: a clean episode
recording both the passed-in character and the environment's milestone
unmodified, and the bot-failure statuses (InvalidAction/BotTimeout/BotError)
still recording character while forcing milestone to None -- extending M1's
progress=0.0/ascended=False zeroing. Does not re-cover the status/progress
mapping itself (see tests/test_trajectory.py).
"""

import pytest

import nethackers.arena.trajectory as T
from nethackers.arena.environment import EnvironmentMetrics
from nethackers.arena.sandbox import BotError, BotTimeout, InvalidAction
from nethackers.contracts.models import Objective


def test_run_trajectory_records_character_and_milestone_on_completion(monkeypatch):
    class FakeEnv:
        action_count = 8

        def reset(self, spec):
            return {"blstats": [0] * 27}

        def step(self, a):
            return {"blstats": [0] * 27}, 0.0, True, False

        def metrics(self):
            return EnvironmentMetrics(0.3, 5, 2, False, "died", "Dlvl:5")

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def reset(self, obs):
            pass

        def act(self, obs):
            return 0

        def close(self):
            pass

        def terminate(self):
            pass

    monkeypatch.setattr(T, "make_environment", lambda *a, **k: FakeEnv())
    monkeypatch.setattr(T, "AgentClient", FakeClient)
    obj = Objective(
        character="val-dwa-law-fem",
        max_steps=10,
        no_progress_timeout=10,
        action_timeout_seconds=1.0,
        seed_set="x",
    )
    res = T.run_trajectory(
        submission_path="unused", spec=_spec(), objective=obj, character="val-dwa-law-fem"
    )
    assert res.status == "completed"
    assert res.character == "val-dwa-law-fem"
    assert res.milestone == "Dlvl:5"


@pytest.mark.parametrize(
    ("bot_exception", "expected_status"),
    [
        (InvalidAction, "invalid_action"),
        (BotTimeout, "bot_timeout"),
        (BotError, "bot_error"),
    ],
)
def test_run_trajectory_zeroes_milestone_but_keeps_character_on_bot_failure(
    monkeypatch, bot_exception, expected_status
):
    class FakeEnv:
        action_count = 8

        def reset(self, spec):
            return {"blstats": [0] * 27}

        def step(self, a):
            return {"blstats": [0] * 27}, 0.0, True, False

        def metrics(self):
            # The environment recorded real progress/milestone before the bot
            # failed; the bot-failure statuses must still zero them out (M1's
            # progress/ascended rule extended to milestone).
            return EnvironmentMetrics(0.7, 9, 4, True, "ascended", "Astral Plane")

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def reset(self, obs):
            pass

        def act(self, obs):
            raise bot_exception("simulated bot failure")

        def close(self):
            pass

        def terminate(self):
            pass

    monkeypatch.setattr(T, "make_environment", lambda *a, **k: FakeEnv())
    monkeypatch.setattr(T, "AgentClient", FakeClient)
    obj = Objective(
        character="val-dwa-law-fem",
        max_steps=10,
        no_progress_timeout=10,
        action_timeout_seconds=1.0,
        seed_set="x",
    )
    res = T.run_trajectory(
        submission_path="unused", spec=_spec(), objective=obj, character="val-dwa-law-fem"
    )
    assert res.status == expected_status
    assert res.progress == 0.0
    assert res.ascended is False
    assert res.milestone is None
    assert res.character == "val-dwa-law-fem"


def _spec():
    from nethackers.arena.seeds import trajectory_spec

    return trajectory_spec("public", "test", 0)
