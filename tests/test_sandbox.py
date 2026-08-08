"""Exercise the spawn-subprocess sandbox's failure taxonomy against fixture bots.

No NLE dependency: OBS is a minimal stand-in observation, and each fixture bot
ignores its contents. These tests only check the sandbox's own contract
(valid action passthrough, invalid-action rejection, per-act timeout) — not
anything about real NetHack observations or actions.
"""

from pathlib import Path

import pytest

from nethackers.arena.sandbox import AgentClient, BotTimeout, InvalidAction

FIX = Path(__file__).parent / "fixtures" / "bots"
OBS = {"blstats": [0] * 27}


def _client(name, **kw):
    return AgentClient(FIX / name, bot_seed=0, action_count=8, timeout_seconds=1.0, **kw)


def test_valid_bot_returns_action_index():
    c = _client("valid_bot")
    try:
        c.reset(OBS)
        assert c.act(OBS) == 0
    finally:
        c.close()


def test_invalid_bot_returns_bool_raises_invalid_action():
    c = _client("invalid_bot")
    try:
        c.reset(OBS)
        with pytest.raises(InvalidAction):
            c.act(OBS)
    finally:
        c.terminate()


def test_wait_bot_exceeds_timeout_raises_bot_timeout():
    c = _client("wait_bot")
    try:
        c.reset(OBS)
        with pytest.raises(BotTimeout):
            c.act(OBS)
    finally:
        c.terminate()
