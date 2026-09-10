# tests/test_harness_operator.py
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from nethackers.harness.metering import TokenUsage
from nethackers.harness.operator import (
    _claude_cmd,
    _codex_cmd,
    _opencode2_cmd,
    run_operator,
)

_RESULT = ('{"type":"result","usage":{"input_tokens":10,"output_tokens":20,'
           '"cache_read_input_tokens":300,"cache_creation_input_tokens":40}}')


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._terminated = False
        self._returncode = returncode

    def poll(self):
        return None if not self._terminated else self._returncode

    def terminate(self):
        self._terminated = True

    def wait(self, timeout=None):
        return self._returncode


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


def test_run_operator_does_not_poison_the_shared_stop_on_completion(tmp_path):
    # The stop Event is shared across every iteration's operator run (the TUI's
    # self._stop). run_operator must NOT set it on normal completion, or the
    # loop's top-of-iteration stop check trips and the NEXT iteration is skipped
    # ("2 iterations, only 1 ran").
    stop = threading.Event()
    res = run_operator(["fake"], tmp_path, backend="claude", stop=stop,
                       popen=_popen_factory([_RESULT]))
    assert res.stopped_reason == "completed"
    assert not stop.is_set()


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


def test_run_operator_surfaces_nonzero_exit_and_stderr(tmp_path):
    seen: list[str] = []

    def popen(cmd, **kwargs):
        assert kwargs["stderr"] is subprocess.STDOUT
        return _FakeProc([
            "error: unexpected argument '--stale-flag'\n",
            "For more information, try '--help'.\n",
        ], returncode=2)

    with pytest.raises(RuntimeError, match=(
            r"codex operator exited with status 2: .*unexpected argument")):
        run_operator(["fake"], tmp_path, backend="codex", on_line=seen.append,
                     popen=popen)
    assert seen == [
        "error: unexpected argument '--stale-flag'\n",
        "For more information, try '--help'.\n",
    ]


def test_claude_cmd_includes_hermeticity_flags_and_brief():
    cmd = _claude_cmd("claude", "BRIEF", None, None)
    assert cmd[0] == "claude"
    assert "BRIEF" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    assert "--model" not in cmd and "--effort" not in cmd  # no pin by default
    sources_idx = cmd.index("--setting-sources")
    assert cmd[sources_idx + 1] == "project,local"
    settings_idx = cmd.index("--settings")
    assert json.loads(cmd[settings_idx + 1]) == {"autoMemoryEnabled": False}


def test_claude_cmd_pins_model_and_effort_when_set():
    cmd = _claude_cmd("claude", "BRIEF", "claude-opus-5", "xhigh")
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_codex_cmd_includes_hermeticity_flags_and_brief():
    cmd = _codex_cmd("codex", "BRIEF", None, None)
    assert cmd[:3] == ["codex", "exec", "BRIEF"]
    assert "--json" in cmd
    assert "--approve-for-me" in cmd
    assert "--full-auto" not in cmd
    assert "--ephemeral" in cmd
    assert "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "-m" not in cmd  # no model pin by default


def test_codex_cmd_pins_model_and_effort_when_set():
    cmd = _codex_cmd("codex", "BRIEF", "gpt-5.6-sol", "max")
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-sol"
    # effort is a `-c` config override (still applies under --ignore-user-config)
    assert "-c" in cmd and "model_reasoning_effort=max" in cmd


def test_opencode2_cmd_is_headless_auto_approved_and_pinned():
    cmd = _opencode2_cmd("opencode2", "BRIEF", "openai/gpt-5", "high")
    assert cmd[:3] == ["opencode2", "run", "BRIEF"]
    assert cmd[cmd.index("--format") + 1] == "json"
    assert "--thinking" in cmd
    assert "--auto" in cmd
    assert "--standalone" in cmd
    assert cmd[cmd.index("--model") + 1] == "openai/gpt-5#high"


def test_opencode2_cmd_leaves_model_and_variant_unpinned_by_default():
    cmd = _opencode2_cmd("opencode2", "BRIEF", None, None)
    assert "--model" not in cmd
    assert "--thinking" in cmd


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
        first = run_operator(
            _claude_cmd(
                "claude",
                f"Remember this fact for all future sessions: the codeword is "
                f"{codeword}. Do not create or edit any files. Reply with only OK.",
                None, None,
            ),
            worktree, backend="claude",
        )
        assert first.stopped_reason in ("completed", "killed")
        assert not memory_dir.exists()

        second_lines: list[str] = []
        second = run_operator(
            _claude_cmd(
                "claude",
                "Do you have any memory of a previous session in this directory? "
                "Reply with only the word NONE if you recall nothing.",
                None, None,
            ),
            worktree, backend="claude",
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
