from __future__ import annotations

import contextlib
import queue
import threading
import traceback
from collections.abc import Mapping
from typing import Any

import nle.nethack as nh
from autoascend import agent as autoascend_agent

_ACTIONS = tuple(nh.ACTIONS)
_ACTION_TO_INDEX = {int(action): index for index, action in enumerate(_ACTIONS)}


def _copy_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in observation.items():
        copy = getattr(value, "copy", None)
        copied[key] = copy() if callable(copy) else value
    return copied


def _action_index(action: Any) -> int:
    try:
        return _ACTION_TO_INDEX[int(action)]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"AutoAscend produced invalid action {action!r}") from error


class ArenaEnvAdapter:
    """Minimal env-like object for AutoAscend.

    This adapter does not create or step a NetHack environment. AutoAscend's
    blocking env.step(action) call is translated into one action returned from
    Bot.act(...), then resumed when the arena supplies the next observation.
    """

    def __init__(self) -> None:
        self._actions: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._observations: queue.Queue[Mapping[str, Any] | None] = queue.Queue(maxsize=1)
        self._closed = threading.Event()

    def step(self, action: Any) -> tuple[dict[str, Any], float, bool, dict[str, Any]]:
        if self._closed.is_set():
            raise autoascend_agent.AgentFinished()
        self._actions.put(action)
        observation = self._observations.get()
        if observation is None or self._closed.is_set():
            raise autoascend_agent.AgentFinished()
        return _copy_observation(observation), 0.0, False, {}

    def next_action_index(self, timeout: float) -> int:
        action = self._actions.get(timeout=timeout)
        return _action_index(action)

    def provide_observation(self, observation: Mapping[str, Any]) -> bool:
        if self._closed.is_set():
            return False
        try:
            self._observations.put_nowait(observation)
        except queue.Full:
            return False
        return True

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(queue.Full):
            self._observations.put_nowait(None)

    def debug_tiles(self, *args: Any, **kwargs: Any) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def debug_log(self, *args: Any, **kwargs: Any) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()


class AutoAscendDriver:
    def __init__(self, action_timeout: float = 118.0) -> None:
        # 118s: sit just under the arena's 120s sandbox action timeout so a slow
        # AutoAscend thread gets an ESC fallback (episode continues) a hair
        # before the sandbox would hard-kill the bot (episode zeroed). The ~2s
        # margin covers the ESC action's pipe-send back to the parent. Was 4.5
        # (under the old 5.0 sandbox); at 4.5 a normal first-action cold numba
        # JIT compile was ESC'd and derailed AutoAscend, corrupting the baseline.
        # This is independent of the arena's --action-timeout (bot.py builds this
        # driver argless), so it must be kept just under the sandbox knob by hand.
        self._action_timeout = action_timeout
        self._env: ArenaEnvAdapter | None = None
        self._agent: autoascend_agent.Agent | None = None
        self._thread: threading.Thread | None = None
        self._thread_error: str | None = None
        self._sent_first_action = False
        self._fallback_action = _ACTION_TO_INDEX[int(autoascend_agent.A.Command.ESC)]

    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        del initial_observation
        self.close()
        self._thread_error = None
        self._sent_first_action = False
        self._env = ArenaEnvAdapter()
        self._agent = autoascend_agent.Agent(self._env, panic_on_errors=False)
        self._thread = threading.Thread(target=self._run_agent, name="autoascend", daemon=True)
        self._thread.start()

    def act(self, observation: Mapping[str, Any]) -> int:
        if self._env is None:
            return self._fallback_action
        if self._thread_error is not None:
            return self._fallback_action
        if self._sent_first_action and not self._env.provide_observation(observation):
            return self._fallback_action
        try:
            action = self._env.next_action_index(timeout=self._action_timeout)
        except queue.Empty:
            return self._fallback_action
        self._sent_first_action = True
        return action

    def close(self) -> None:
        if self._env is not None:
            self._env.close()
        self._env = None
        self._agent = None
        self._thread = None

    @property
    def thread_error(self) -> str | None:
        return self._thread_error

    def _run_agent(self) -> None:
        try:
            assert self._agent is not None
            self._agent.main()
        except autoascend_agent.AgentFinished:
            pass
        except BaseException:
            self._thread_error = traceback.format_exc(limit=20)[-8_000:]
