"""Exercise run_trajectory's reset/act/step loop for the clean-completion path.

No NLE dependency and no real bot subprocess: FakeEnv/FakeClient stand in for
NLEEnvironment/AgentClient by monkeypatching trajectory.make_environment and
trajectory.AgentClient. This checks only that a clean episode drives the loop
correctly and that the returned TrajectoryResult reflects env.metrics() with
status "completed". It does not exercise the exception -> ResultStatus
mapping (invalid_action/bot_timeout/bot_error/infrastructure_error), real
NetHack observations/actions, or the sandboxed subprocess/timeout behavior
covered separately by tests/test_sandbox.py.
"""

import nethackers.arena.trajectory as T
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


def _spec():
    from nethackers.arena.seeds import trajectory_spec

    return trajectory_spec("public", "test", 0)
