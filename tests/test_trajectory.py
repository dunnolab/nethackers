"""Exercise run_trajectory's episode loop and status/scoring mapping against
fakes.

No NLE dependency and no real bot subprocess: FakeEnv/FakeClient stand in for
NLEEnvironment/AgentClient by monkeypatching trajectory.make_environment and
trajectory.AgentClient. Covers: a clean episode mapping to "completed" with
env.metrics() passed through unmodified, and InvalidAction/BotTimeout/
BotError raised from FakeClient.act mapping to their respective statuses
with progress forced to 0.0 and ascended forced to False even when
env.metrics() reports real progress (the plan's Global Constraint: "bot
errors/timeouts -> progress 0.0"). Does not cover infrastructure_error, real
NetHack observations/actions, or the sandboxed subprocess/timeout behavior
covered separately by tests/test_sandbox.py.
"""

import pytest

import nethackers.arena.trajectory as T
from nethackers.arena.sandbox import BotError, BotTimeout, InvalidAction
from nethackers.contracts.models import Objective


def test_run_trajectory_maps_a_clean_episode_to_completed(monkeypatch):
    class FakeEnv:
        action_count = 8

        def reset(self, spec):
            return {"blstats": [0] * 27}

        def step(self, a):
            return {"blstats": [0] * 27}, 0.0, True, False

        def metrics(self):
            from nethackers.arena.environment import EnvironmentMetrics

            return EnvironmentMetrics(0.3, 5, 2, False, "died")

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
        character=None,
        max_steps=10,
        no_progress_timeout=10,
        action_timeout_seconds=1.0,
        seed_set="x",
    )
    res = T.run_trajectory(submission_path="unused", spec=_spec(), objective=obj, action_count=8)
    assert res.status == "completed" and abs(res.progress - 0.3) < 1e-9


@pytest.mark.parametrize(
    ("bot_exception", "expected_status"),
    [
        (InvalidAction, "invalid_action"),
        (BotTimeout, "bot_timeout"),
        (BotError, "bot_error"),
    ],
)
def test_run_trajectory_zeroes_progress_and_ascended_on_bot_failure(
    monkeypatch, bot_exception, expected_status
):
    class FakeEnv:
        action_count = 8

        def reset(self, spec):
            return {"blstats": [0] * 27}

        def step(self, a):
            return {"blstats": [0] * 27}, 0.0, True, False

        def metrics(self):
            from nethackers.arena.environment import EnvironmentMetrics

            # The environment recorded real progress before the bot failed;
            # the bot-failure statuses must still score zero.
            return EnvironmentMetrics(0.7, 9, 4, True, "ascended")

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
        character=None,
        max_steps=10,
        no_progress_timeout=10,
        action_timeout_seconds=1.0,
        seed_set="x",
    )
    res = T.run_trajectory(submission_path="unused", spec=_spec(), objective=obj, action_count=8)
    assert res.status == expected_status
    assert res.progress == 0.0
    assert res.ascended is False


def _spec():
    from nethackers.arena.seeds import trajectory_spec

    return trajectory_spec("public", "test", 0)
