# tests/test_container_operator.py
from pathlib import Path

import pytest

from nethackers.harness.container_operator import ContainerCaps, build_docker_argv


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
