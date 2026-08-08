import pytest

from nethackers.arena.environment import PUBLIC_OBSERVATION_KEYS, disable_autopickup


def test_public_keys_are_the_fourteen():
    assert len(PUBLIC_OBSERVATION_KEYS) == 14
    assert "tty_chars" in PUBLIC_OBSERVATION_KEYS and "blstats" in PUBLIC_OBSERVATION_KEYS


def test_disable_autopickup_prepends_negation_and_dedupes():
    assert disable_autopickup(("autopickup", "color")) == ("!autopickup", "color")
    assert disable_autopickup(("color",))[0] == "!autopickup"


@pytest.mark.nle
def test_reset_returns_public_keys_for_a_fixed_character():
    from nethackers.arena.environment import make_environment
    from nethackers.arena.seeds import trajectory_spec

    env = make_environment(max_steps=50, no_progress_timeout=50, character="val-dwa-law-fem")
    obs = env.reset(trajectory_spec("public", "test", 0))
    try:
        assert set(("blstats", "tty_chars")).issubset(obs.keys())
        assert env.action_count > 0
    finally:
        env.close()
