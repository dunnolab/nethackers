# tests/test_harness_operator.py
import json
import shutil
from pathlib import Path

import pytest

from nethackers.harness.metering import TokenUsage
from nethackers.harness.operator import (
    ClaudeOperator,
    _claude_cmd,
    _codex_cmd,
    run_operator,
)

_RESULT = ('{"type":"result","usage":{"input_tokens":10,"output_tokens":20,'
           '"cache_read_input_tokens":300,"cache_creation_input_tokens":40}}')


class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self._terminated = False

    def poll(self):
        return None if not self._terminated else 0

    def terminate(self):
        self._terminated = True

    def wait(self, timeout=None):
        return 0


def _popen_factory(lines):
    # widened to **kwargs: run_operator passes start_new_session (+ the pipe args)
    def popen(cmd, **kwargs):
        return _FakeProc(lines)
    return popen


def test_run_operator_meters_faithfully_from_result_line(tmp_path):
    res = run_operator(["fake"], tmp_path, backend="claude",
                       popen=_popen_factory(["ignored", _RESULT]))
    assert res.usage == TokenUsage(10, 20, 40, 300)   # cache_creation=40, cache_read=300
    assert res.total == 370 and res.stopped_reason == "completed"


def test_run_operator_streams_every_line(tmp_path):
    seen: list[str] = []
    run_operator(["fake"], tmp_path, backend="claude", on_line=seen.append,
                 popen=_popen_factory(["a", "b", _RESULT]))
    assert seen == ["a", "b", _RESULT]


def test_run_operator_on_line_exception_never_aborts(tmp_path):
    def boom(_line):
        raise ValueError("display crashed")
    res = run_operator(["fake"], tmp_path, backend="claude", on_line=boom,
                       popen=_popen_factory([_RESULT]))
    assert res.total == 370   # metering + reap unaffected by the callback crash


def test_run_operator_reaps_the_process(tmp_path):
    proc_box: dict = {}

    class _TrackWait(_FakeProc):
        def wait(self, timeout=None):
            proc_box["waited"] = True
            return 0

    def popen(cmd, **kwargs):
        proc_box["proc"] = _TrackWait([_RESULT])
        return proc_box["proc"]

    run_operator(["fake"], tmp_path, backend="claude", popen=popen)
    assert proc_box.get("waited") is True


def test_claude_cmd_includes_hermeticity_flags_and_brief():
    cmd = _claude_cmd("claude", "BRIEF")
    assert cmd[0] == "claude"
    assert "BRIEF" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    sources_idx = cmd.index("--setting-sources")
    assert cmd[sources_idx + 1] == "project,local"
    settings_idx = cmd.index("--settings")
    assert json.loads(cmd[settings_idx + 1]) == {"autoMemoryEnabled": False}


def test_codex_cmd_includes_hermeticity_flags_and_brief():
    cmd = _codex_cmd("codex", "BRIEF")
    assert cmd[:3] == ["codex", "exec", "BRIEF"]
    assert "--json" in cmd
    assert "--full-auto" in cmd
    assert "--ephemeral" in cmd
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd
    assert "--skip-git-repo-check" in cmd


def _claude_project_slug(cwd: Path) -> str:
    """Mirror Claude Code's cwd -> ~/.claude/projects/<slug> mapping: the
    absolute path with every "/" and "." replaced by "-"."""
    return str(cwd).replace("/", "-").replace(".", "-")


@pytest.mark.claude_live
def test_claude_operator_does_not_recall_memory_across_runs(tmp_path):
    """Regression for the confirmed root cause (see
    docs/superpowers/specs/2026-08-11-hermetic-operator-design.md): before the
    hermeticity flags, a second `claude -p` run in the same reused worktree cwd
    recalled an earlier run's auto-written memory. Needs the real `claude` CLI +
    network -- gated behind `claude_live`."""
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    project_dir = Path.home() / ".claude" / "projects" / _claude_project_slug(worktree)
    memory_dir = project_dir / "memory"
    codeword = "BUG-MARKER-7f3a1c9e"

    try:
        op = ClaudeOperator()
        first = op.run(
            worktree,
            f"Remember this fact for all future sessions: the codeword is "
            f"{codeword}. Do not create or edit any files. Reply with only OK.",
        )
        assert first.stopped_reason in ("completed", "killed")
        assert not memory_dir.exists()

        second_lines: list[str] = []
        second = op.run(
            worktree,
            "Do you have any memory of a previous session in this directory? "
            "Reply with only the word NONE if you recall nothing.",
            on_line=second_lines.append,
        )
        assert second.stopped_reason in ("completed", "killed")
        assert not memory_dir.exists()

        stdout = "\n".join(second_lines).lower()
        for recall_marker in ("restored", "prior iteration", "previously-validated"):
            assert recall_marker not in stdout
        assert codeword.lower() not in stdout
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
