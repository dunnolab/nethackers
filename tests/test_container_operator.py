# tests/test_container_operator.py
import threading
from pathlib import Path

import pytest

from nethackers.harness.container_operator import (
    ContainerCaps,
    ContainerOperator,
    build_docker_argv,
)


def _argv(harness, **kw):
    return build_docker_argv(
        harness=harness, image="nethackers/mutator:test", name="mut-r-1",
        worktree=Path("/runs/r/work/iter-1"), cli=None, model="gpt-x", effort="high",
        caps=ContainerCaps(), auth_args=["-v", "/h/.codex:/home/agent/.codex"],
        brief="B", **kw)


def test_docker_run_shape_and_caps():
    a = _argv("codex")
    assert a[:3] == ["docker", "run", "--rm"]
    assert "--name" in a and "mut-r-1" in a
    for cap in ("--pids-limit", "512", "--memory", "8g", "--cpus", "4"):
        assert cap in a
    assert a[a.index("--security-opt") + 1] == "no-new-privileges"
    # swap must be capped too, else a runaway reaches ~2x --memory via swap
    assert a[a.index("--memory-swap") + 1] == ContainerCaps().memory
    assert "/runs/r/work/iter-1:/workspace" in a
    assert a[a.index("-w") + 1] == "/workspace"
    assert "nethackers/mutator:test" in a
    # auth args are present and precede the image
    assert a.index("-v") < a.index("nethackers/mutator:test")
    # wall-clock timeout wraps the in-container command
    assert "timeout" in a and str(ContainerCaps().timeout_s) in a


def test_codex_in_cage_bypasses_its_own_sandbox():
    a = _argv("codex")
    assert "--dangerously-bypass-approvals-and-sandbox" in a
    assert "--approve-for-me" not in a           # replaced, not appended
    assert "exec" in a                            # still `codex exec`


def test_claude_in_cage_skips_permissions():
    a = _argv("claude")
    assert "--dangerously-skip-permissions" in a
    assert "-p" in a


def test_unknown_harness_raises():
    with pytest.raises(ValueError, match="unknown harness"):
        _argv("pi")


class FakePopen:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.stdout = iter(['{"type":"x"}\n'])
        self.pid = 4321
        self.returncode = 0
    def poll(self): return 0
    def wait(self, timeout=None): return 0


def test_run_delegates_and_builds_docker_argv(monkeypatch, tmp_path):
    seen = {}
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-3"
    wt.mkdir(parents=True)
    # NB: `and`, not `or` -- dict.setdefault returns the (truthy) cmd list
    # itself, so `or` would short-circuit and hand run_operator a bare list
    # instead of a FakePopen, breaking on the first `proc.stdout` access.
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    res = op.run(wt, "BRIEF-TEXT")
    assert seen["cmd"][:3] == ["docker", "run", "--rm"]
    assert "BRIEF-TEXT" in seen["cmd"]        # real brief threaded into the inner cmd
    assert res.backend == "codex"


def test_stop_docker_kills_named_container(tmp_path):
    killed = []
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    op._docker_kill = lambda name: killed.append(name)
    # drive the stop-watcher directly (unit): a set event → docker kill invoked
    stop = threading.Event()
    stop.set()
    op._maybe_kill_on_stop("mut-work-iter-9", stop)
    assert killed == ["mut-work-iter-9"]


class _GatedFakePopen:
    """Like FakePopen, but its stdout blocks until `release` fires -- so the
    background stop-watcher is guaranteed a chance to act BEFORE run_operator
    can finish. Without this gate, a real (instant) FakePopen would let
    run_operator complete before the watcher's first 0.1s poll tick, making
    a same-name assertion racy: it would pass or fail depending on thread
    scheduling, not on whether run() is actually correct. poll() must stay
    non-None (never "still running") so run_operator's OWN internal watcher
    never attempts a real os.killpg on this fake/unrelated pid."""

    def __init__(self, cmd, release, **kw):
        self.cmd = cmd
        self.pid = 4321
        self._release = release

    @property
    def stdout(self):
        self._release.wait(timeout=2)
        return iter(['{"type":"x"}\n'])

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_stop_kills_the_same_name_baked_into_the_docker_argv(tmp_path):
    """run() computes the container name once; a silent divergence between
    the --name it bakes into the docker argv and the name its stop-watcher
    docker-kills would have `docker kill` target the wrong (or a
    nonexistent) container, so stop would fail silently. Pin that both call
    sites see the exact same value out of a single run() invocation."""
    seen = {}
    killed = []
    release = threading.Event()

    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-7"
    wt.mkdir(parents=True)

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return _GatedFakePopen(cmd, release, **kw)
    op._popen = fake_popen

    def fake_docker_kill(name):
        killed.append(name)
        release.set()  # let the gated stdout resolve now that the kill fired
    op._docker_kill = fake_docker_kill

    stop = threading.Event()
    stop.set()
    op.run(wt, "BRIEF-TEXT", stop=stop)

    name_in_argv = seen["cmd"][seen["cmd"].index("--name") + 1]
    assert name_in_argv == "mut-work-iter-7"
    assert killed == [name_in_argv]   # the SAME name -- not just the same formula
