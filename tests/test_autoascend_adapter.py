"""Worker failures must reach the arena even while act() is waiting on a queue."""

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def adapter(monkeypatch):
    # Exercise the real adapter without importing the optional NLE/AutoAscend
    # dependency trees. The worker itself is controlled by each test.
    nh = ModuleType("nle.nethack")
    monkeypatch.setattr(nh, "ACTIONS", (27, 46), raising=False)
    nle = ModuleType("nle")
    monkeypatch.setattr(nle, "nethack", nh, raising=False)
    agent = ModuleType("autoascend.agent")
    monkeypatch.setattr(agent, "AgentFinished", type("AgentFinished", (Exception,), {}),
                        raising=False)
    monkeypatch.setattr(agent, "A", SimpleNamespace(Command=SimpleNamespace(ESC=27)),
                        raising=False)
    package = ModuleType("autoascend")
    monkeypatch.setattr(package, "agent", agent, raising=False)
    for name, module in (("nle", nle), ("nle.nethack", nh),
                         ("autoascend", package), ("autoascend.agent", agent)):
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[1] / "roots/autoascend/arena_adapter.py"
    spec = importlib.util.spec_from_file_location("tested_arena_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("exception", [ValueError, SystemExit])
def test_worker_failure_before_act_is_reported(adapter, monkeypatch, exception):
    class Agent:
        def __init__(self, env, **kwargs):
            pass

        def main(self):
            raise exception("worker exploded")

    monkeypatch.setattr(adapter.autoascend_agent, "Agent", Agent, raising=False)
    driver = adapter.AutoAscendDriver()
    try:
        driver.reset({})
        driver._thread.join(timeout=2)
        assert not driver._thread.is_alive()
        with pytest.raises(RuntimeError, match="worker exploded"):
            driver.act({})
        assert exception.__name__ in driver.thread_error
    finally:
        driver.close()


def test_failure_wakes_act_waiting_for_an_action(adapter, monkeypatch):
    waiting = threading.Event()

    class Agent:
        def __init__(self, env, **kwargs):
            pass

        def main(self):
            assert waiting.wait(timeout=2)
            raise ValueError("failed during act")

    monkeypatch.setattr(adapter.autoascend_agent, "Agent", Agent, raising=False)
    driver = adapter.AutoAscendDriver(action_timeout=5)
    driver.reset({})
    worker = driver._thread
    original = driver._env.next_action_index

    def next_action_index(timeout):
        waiting.set()
        return original(timeout)

    monkeypatch.setattr(driver._env, "next_action_index", next_action_index)
    try:
        with pytest.raises(RuntimeError, match="failed during act"):
            driver.act({})
    finally:
        waiting.set()
        worker.join(timeout=2)
        driver.close()


def test_late_error_from_previous_episode_does_not_poison_reset(adapter, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    instances = []

    class Agent:
        def __init__(self, env, **kwargs):
            self.env = env
            instances.append(self)

        def main(self):
            if self is instances[0]:
                started.set()
                assert release.wait(timeout=2)
                raise ValueError("old episode")
            self.env.step(46)

    monkeypatch.setattr(adapter.autoascend_agent, "Agent", Agent, raising=False)
    driver = adapter.AutoAscendDriver(action_timeout=2)
    driver.reset({})
    old_worker = driver._thread
    try:
        assert started.wait(timeout=2)
        driver.reset({})
        release.set()
        old_worker.join(timeout=2)
        assert not old_worker.is_alive()
        assert driver.thread_error is None
        assert driver.act({}) == 1
    finally:
        release.set()
        driver.close()


def test_healthy_wait_timeout_keeps_escape_fallback(adapter):
    driver = adapter.AutoAscendDriver(action_timeout=0.01)
    driver._env = adapter.ArenaEnvAdapter()
    try:
        assert driver.act({}) == 0
        assert driver.thread_error is None
    finally:
        driver.close()


def test_deep_worker_traceback_keeps_the_failure_origin(adapter, monkeypatch):
    def leaf():
        raise ValueError("deep failure")

    def recurse(depth):
        if depth:
            recurse(depth - 1)
        else:
            leaf()

    class Agent:
        def __init__(self, env, **kwargs):
            pass

        def main(self):
            recurse(30)

    monkeypatch.setattr(adapter.autoascend_agent, "Agent", Agent, raising=False)
    driver = adapter.AutoAscendDriver()
    try:
        driver.reset({})
        driver._thread.join(timeout=2)
        assert not driver._thread.is_alive()
        assert "in leaf" in driver.thread_error
        assert len(driver.thread_error) <= 8000
    finally:
        driver.close()


def test_reporting_failure_does_not_block_on_a_queued_action(adapter):
    env = adapter.ArenaEnvAdapter()
    env._actions.put_nowait(46)
    env.report_error("worker failed")
    assert env.next_action_index(timeout=0.1) == 1
