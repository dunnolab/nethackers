# tests/test_harness_operator.py
import subprocess

from nethackers.harness.operator import (
    OperatorResult,
    _claude_tokens,
    _codex_tokens,
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
