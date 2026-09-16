from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nethackers.contracts.bot import ArenaBot

_cache_root = Path(tempfile.gettempdir()) / "nethack_arena_submission_cache"
_cache_root.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(_cache_root / "xdg"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(_cache_root / "numba"))

from arena_adapter import AutoAscendDriver  # noqa: E402


class Bot:
    def __init__(self) -> None:
        self._driver = AutoAscendDriver()

    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        self._driver.reset(initial_observation)

    def act(self, observation: Mapping[str, Any]) -> int:
        return self._driver.act(observation)

    def close(self) -> None:
        self._driver.close()


def make_agent() -> ArenaBot:
    return Bot()
