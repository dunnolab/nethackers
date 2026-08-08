# tests/test_autoascend_solution.py
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "roots" / "autoascend"


@pytest.mark.nle
def test_autoascend_plays_a_few_steps_without_crashing():
    sys.path.insert(0, str(ROOT))
    try:
        from bot import make_agent  # noqa
        from nethackers.arena.environment import make_environment
        from nethackers.arena.seeds import trajectory_spec
        agent = make_agent()
        env = make_environment(200, 200, character="val-dwa-law-fem")
        obs = env.reset(trajectory_spec("public", "smoke", 0))
        agent.reset(obs)
        for _ in range(50):
            obs, _, terminated, truncated = env.step(agent.act(obs))
            if terminated or truncated:
                break
        assert env.metrics().max_depth >= 1
    finally:
        env.close()
        agent.close()
        sys.path.remove(str(ROOT))
