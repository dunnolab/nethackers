from __future__ import annotations

import importlib
import multiprocessing
import numbers
import os
import random
import sys
import traceback
from collections.abc import Mapping
from contextlib import suppress
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from nethackers.arena.lifetime import die_with_parent
from nethackers.contracts.bot import ArenaBot


class BotError(RuntimeError):
    pass


class BotTimeout(BotError):
    pass


class InvalidAction(BotError):
    pass


def _readonly(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _readonly(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_readonly(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_readonly(item) for item in value)
    if getattr(value, "flags", None) is not None:
        with suppress(AttributeError, ValueError):
            value.setflags(write=False)
    return value


def _load_agent(submission_path: Path) -> ArenaBot:
    if not (submission_path / "bot.py").is_file():
        raise ValueError("submission must contain bot.py")
    sys.path.insert(0, str(submission_path))
    module = importlib.import_module("bot")
    agent = module.make_agent()
    if not callable(getattr(agent, "reset", None)):
        raise TypeError("bot must define reset(initial_observation)")
    if not callable(getattr(agent, "act", None)):
        raise TypeError("bot must define act(observation)")
    return agent


def _agent_process(
    connection: Connection, submission_path: str, bot_seed: int, parent_pid: int
) -> None:
    try:
        # A bot inside act() is not reading the connection, so a dead parent's
        # EOF cannot reach it; only the kernel can (see arena/lifetime.py).
        die_with_parent(parent_pid)
        os.environ.pop("NETHACK_ARENA_SECRET", None)
        os.chdir(submission_path)
        random.seed(bot_seed)
        try:
            import numpy as np

            np.random.seed(bot_seed % (1 << 32))
        except ImportError:
            pass
        agent = _load_agent(Path(submission_path))
        connection.send(("ready", None))
        while True:
            command, payload = connection.recv()
            if command == "reset":
                agent.reset(_readonly(payload))
                connection.send(("reset", None))
            elif command == "act":
                connection.send(("action", agent.act(_readonly(payload))))
            elif command == "close":
                close = getattr(agent, "close", None)
                if callable(close):
                    close()
                connection.send(("closed", None))
                return
            else:
                raise ValueError(f"unknown bot command: {command}")
    except BaseException:
        error = traceback.format_exc(limit=20)[-8_000:]
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send(("error", error))
    finally:
        connection.close()


class AgentClient:
    def __init__(
        self,
        submission_path: Path,
        bot_seed: int,
        action_count: int,
        timeout_seconds: float,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._action_count = action_count
        process_context = multiprocessing.get_context("spawn")
        parent, child = process_context.Pipe(duplex=True)
        self._connection = parent
        self._process = process_context.Process(
            target=_agent_process,
            args=(child, str(submission_path), bot_seed, os.getpid()),
            name="nethack-arena-bot",
            daemon=True,
        )
        self._process.start()
        child.close()
        try:
            kind, payload = self._receive(max(30.0, timeout_seconds), "bot startup")
            if kind == "error":
                raise BotError(payload)
            if kind != "ready":
                raise BotError(f"unexpected bot startup response: {kind}")
        except BaseException:
            self.terminate()
            raise

    def _receive(self, timeout_seconds: float, operation: str) -> tuple[str, Any]:
        if not self._connection.poll(timeout_seconds):
            self.terminate()
            raise BotTimeout(f"{operation} exceeded {timeout_seconds:g} seconds")
        try:
            return self._connection.recv()
        except EOFError as error:
            raise BotError(f"bot exited during {operation}") from error

    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        try:
            self._connection.send(("reset", initial_observation))
        except (BrokenPipeError, EOFError, OSError) as error:
            raise BotError("bot exited before reset") from error
        kind, payload = self._receive(self._timeout_seconds, "reset")
        if kind == "error":
            raise BotError(payload)
        if kind != "reset":
            raise BotError(f"unexpected bot reset response: {kind}")

    def act(self, observation: Mapping[str, Any]) -> int:
        try:
            self._connection.send(("act", observation))
        except (BrokenPipeError, EOFError, OSError) as error:
            raise BotError("bot exited before receiving observation") from error
        kind, payload = self._receive(self._timeout_seconds, "action")
        if kind == "error":
            raise BotError(payload)
        if kind != "action":
            raise BotError(f"unexpected bot response: {kind}")
        if isinstance(payload, bool) or not isinstance(payload, numbers.Integral):
            raise InvalidAction(f"action must be an integer, got {type(payload).__name__}")
        action = int(payload)
        if not 0 <= action < self._action_count:
            raise InvalidAction(
                f"action {action} is outside the valid range [0, {self._action_count})"
            )
        return action

    def close(self) -> None:
        if not self._process.is_alive():
            self._connection.close()
            self._process.join(timeout=1)
            return
        try:
            self._connection.send(("close", None))
            if self._connection.poll(1):
                self._connection.recv()
        except (BrokenPipeError, EOFError, OSError):
            pass
        finally:
            self.terminate()

    def terminate(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        self._connection.close()
