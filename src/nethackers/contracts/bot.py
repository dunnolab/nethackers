from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ArenaBot(Protocol):
    """Protocol implemented by a NetHack Arena submission agent.

    The evaluator creates one bot with ``make_agent()``, calls
    ``reset(initial_observation)`` once at the start of each episode, then calls
    ``act(observation)`` until the episode ends. Observations are mappings of
    public NLE observation keys to read-only values. Actions must be integer
    indices into ``nle.nethack.ACTIONS``.
    """

    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        """Start a new episode and inspect its initial observation."""

    def act(self, observation: Mapping[str, Any]) -> int:
        """Return an integer index into ``nle.nethack.ACTIONS``."""


class ClosableArenaBot(ArenaBot, Protocol):
    """Optional extension for bots that need cleanup after an episode."""

    def close(self) -> None:
        """Release resources held by the bot process."""


def make_agent() -> ArenaBot:
    """Document the required submission factory signature.

    This function is a protocol example, not an implementation. Submissions must
    define their own top-level ``make_agent()`` in ``submission/bot.py``.
    """
    raise NotImplementedError("submissions must define make_agent() in submission/bot.py")
