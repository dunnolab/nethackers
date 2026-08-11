# tests/test_harness_operator.py
import subprocess

from nethackers.harness.operator import (
    OperatorResult,
    _claude_tokens,
    _codex_tokens,
    agent_tokens,
    run_with_token_budget,
)


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
    def popen(cmd, cwd, stdout, stderr, text, bufsize):
        return _FakeProc(lines)
    return popen


def _tokens(line):  # each canned line is "<n>"
    return int(line.strip())


def test_stops_on_budget_with_overshoot(tmp_path):
    # steps of 40 tokens each; budget 100 -> stop after the step that crosses (120)
    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=100, timeout_s=999,
        tokens_from_line=_tokens, backend="fake",
        popen=_popen_factory(["40", "40", "40", "40"]))
    assert res == OperatorResult(backend="fake", tokens=120, stopped_reason="budget")


def test_completes_under_budget(tmp_path):
    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=1000, timeout_s=999,
        tokens_from_line=_tokens, backend="fake",
        popen=_popen_factory(["10", "20"]))
    assert res.tokens == 30 and res.stopped_reason == "completed"


def test_timeout(tmp_path):
    ticks = iter([0.0, 0.0, 5.0])  # third read is past the 1s timeout
    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=10_000, timeout_s=1.0,
        tokens_from_line=_tokens, backend="fake",
        popen=_popen_factory(["10", "10", "10"]), monotonic=lambda: next(ticks))
    assert res.stopped_reason == "timeout"


class _FakeProcIgnoresSigterm(_FakeProc):
    """A process that ignores SIGTERM: the first `.wait(timeout=...)` call
    times out, so the caller must fall back to `.kill()` + a final wait."""

    def __init__(self, lines):
        super().__init__(lines)
        self.killed = False
        self._wait_calls = 0

    def wait(self, timeout=None):
        self._wait_calls += 1
        if self._wait_calls == 1:
            raise subprocess.TimeoutExpired("x", 30)
        return 0

    def kill(self):
        self.killed = True


def test_kill_fallback_when_process_ignores_sigterm(tmp_path):
    proc_box: dict[str, _FakeProcIgnoresSigterm] = {}

    def popen(cmd, cwd, stdout, stderr, text, bufsize):
        proc = _FakeProcIgnoresSigterm(["10", "20"])
        proc_box["proc"] = proc
        return proc

    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=10_000, timeout_s=999,
        tokens_from_line=_tokens, backend="fake", popen=popen)
    assert isinstance(res, OperatorResult)
    assert proc_box["proc"].killed is True


def test_claude_tokens_non_dict_json_is_zero():
    assert _claude_tokens("[1,2,3]") == 0


def test_claude_tokens_null_input_tokens_is_null_safe():
    line = '{"message":{"usage":{"input_tokens":null,"output_tokens":5}}}'
    assert _claude_tokens(line) == 5


def test_codex_tokens_non_dict_json_is_zero():
    assert _codex_tokens("42") == 0


def test_claude_tokens_message_is_string_does_not_crash():
    # The "'str' object has no attribute 'get'" crash: a stream line whose
    # "message" is a string (not a dict) must yield 0, not raise.
    assert _claude_tokens('{"type":"system","message":"init"}') == 0
    # Usage nested under "message" (assistant turn) is summed.
    assert _claude_tokens('{"message":{"usage":{"input_tokens":3,"output_tokens":4}}}') == 7
    # Usage at the top level (result turn) is summed.
    assert _claude_tokens('{"type":"result","usage":{"input_tokens":5,"output_tokens":6}}') == 11


def test_on_line_receives_every_stdout_line(tmp_path):
    seen: list[str] = []
    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=10_000, timeout_s=999,
        tokens_from_line=_tokens, backend="fake",
        popen=_popen_factory(["10", "20", "30"]), on_line=seen.append)
    assert seen == ["10", "20", "30"]
    assert res.tokens == 60


def test_on_line_sees_the_budget_crossing_line(tmp_path):
    seen: list[str] = []
    run_with_token_budget(
        ["fake"], tmp_path, token_budget=50, timeout_s=999,
        tokens_from_line=_tokens, backend="fake",
        popen=_popen_factory(["40", "40", "40"]), on_line=seen.append)
    assert seen == ["40", "40"]  # the line that crosses is still forwarded


def test_agent_tokens_dispatches_by_backend():
    assert agent_tokens("claude",
                        '{"message":{"usage":{"input_tokens":3,"output_tokens":4}}}') == 7
    assert agent_tokens("codex", '{"usage":{"total_tokens":9}}') == 9
    assert agent_tokens("unknown", "{}") == 0


def test_on_line_exception_does_not_abort_or_leak(tmp_path):
    """Verify that a misbehaving on_line callback cannot crash the run or
    leak the subprocess. The exception must be swallowed, the OperatorResult
    must still be valid, and the process must still be reaped."""

    class _FakeProcTrackingWait(_FakeProc):
        def __init__(self, lines):
            super().__init__(lines)
            self.wait_called = False

        def wait(self, timeout=None):
            self.wait_called = True
            return super().wait(timeout)

    proc_box: dict[str, _FakeProcTrackingWait] = {}

    def popen(cmd, cwd, stdout, stderr, text, bufsize):
        proc = _FakeProcTrackingWait(["10", "20", "30"])
        proc_box["proc"] = proc
        return proc

    def crashing_callback(line: str) -> None:
        raise ValueError(f"display callback crashed on line: {line}")

    # Despite the callback raising on every line, the run should complete
    # normally, returning a valid OperatorResult with correct token total
    res = run_with_token_budget(
        ["fake"], tmp_path, token_budget=10_000, timeout_s=999,
        tokens_from_line=_tokens, backend="fake",
        popen=popen, on_line=crashing_callback)

    # Verify process was reaped (wait was called)
    assert proc_box["proc"].wait_called is True
    # Verify OperatorResult is valid with correct token total
    assert res == OperatorResult(backend="fake", tokens=60, stopped_reason="completed")
