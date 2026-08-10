# tests/test_harness_operator.py
from nethackers.harness.operator import OperatorResult, run_with_token_budget


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
