import numpy as np
import pytest

pytest.importorskip("nle")

from nethackers.arena.environment import make_environment  # noqa: E402
from nethackers.contracts.models import TrajectorySpec  # noqa: E402


@pytest.mark.nle
def test_random_rollout_records_a_cause_or_none():
    """A real (short) NLE episode: random play until the loop ends. Asserts the
    capture path never raises and yields either a non-empty string or None."""
    env = make_environment(max_steps=40_000, no_progress_timeout=40_000, character=None)
    try:
        env.reset(TrajectorySpec(0, 1001, 2001, 3001, 7))  # reset() installs the seeds
        rng = np.random.RandomState(0)
        for _ in range(40_000):
            _obs, _r, terminated, truncated = env.step(int(rng.randint(env.action_count)))
            if terminated or truncated:
                break
        cause = env.metrics().cause_of_death
        assert cause is None or (isinstance(cause, str) and cause)
    finally:
        env.close()
