# tests/test_bot_protocol.py
import typing
from collections.abc import Mapping

from nethackers.contracts.bot import ArenaBot, ClosableArenaBot


class _Bot:
    def reset(self, initial_observation: Mapping) -> None: ...
    def act(self, observation: Mapping) -> int: return 0

def test_structural_conformance():
    bot = _Bot()
    assert hasattr(bot, "reset") and hasattr(bot, "act")
    # Protocols are structural; a conforming object is accepted where ArenaBot is expected.
    def takes(_: ArenaBot) -> None: ...
    takes(bot)  # must type-check and run

def test_closable_is_superset():
    # `Protocol.__protocol_attrs__` is only reliably present on Python 3.12+
    # (this package supports >=3.11). `typing._get_protocol_attrs` computes
    # the same attribute set and is available across 3.11-3.14, so it keeps
    # this test's intent - ClosableArenaBot's protocol attrs are a superset
    # of ArenaBot's - true on every supported runtime.
    assert set(typing._get_protocol_attrs(ArenaBot)) <= set(
        typing._get_protocol_attrs(ClosableArenaBot)
    )
