"""Running one step: captured (pty, live line, tail) and in-terminal logins."""
from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console

from nethackers import ptyrun
from nethackers.setup import runner


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, force_terminal=False)


@pytest.mark.skipif(not ptyrun.available(), reason="needs a POSIX pty")
def test_a_captured_step_that_succeeds():
    result = runner.run_captured(
        (sys.executable, "-c", "print('one'); print('two')"), title="install X",
        console=_console())
    assert result.ok and result.detail == "" and result.tail == ("one", "two")


@pytest.mark.skipif(not ptyrun.available(), reason="needs a POSIX pty")
def test_a_failed_captured_step_keeps_only_its_last_15_lines():
    script = "import sys\nfor i in range(40): print(i)\nsys.exit(3)"
    result = runner.run_captured((sys.executable, "-c", script), title="install X",
                                 console=_console())
    assert not result.ok and result.detail == "exited 3"
    assert result.tail == tuple(str(i) for i in range(25, 40))


@pytest.mark.skipif(not ptyrun.available(), reason="needs a POSIX pty")
def test_captured_steps_run_without_prompts():
    script = (
        "import os; "
        "print(os.environ.get('NONINTERACTIVE'), "
        "os.environ.get('CODEX_NON_INTERACTIVE'))"
    )
    result = runner.run_captured((sys.executable, "-c", script), title="x", console=_console())
    assert result.tail == ("1 1",)


def test_a_terminal_step_reports_the_exit_code():
    ok = runner.run_terminal(
        ("gh", "auth", "login"),
        run=lambda argv: SimpleNamespace(returncode=0)
    )
    bad = runner.run_terminal(
        ("gh", "auth", "login"),
        run=lambda argv: SimpleNamespace(returncode=1)
    )
    assert ok.ok and not bad.ok and bad.detail == "exited 1"


def test_a_missing_tool_is_a_failed_step_not_a_crash():
    def missing(argv):
        raise FileNotFoundError(argv[0])
    result = runner.run_terminal(("claude", "auth", "login"), run=missing)
    assert not result.ok and "`claude` isn't installed" in result.detail


def test_time_formats():
    assert runner.clock_text(21.4) == "0:21" and runner.clock_text(64) == "1:04"
    assert runner.elapsed_text(34.2) == "34 s" and runner.elapsed_text(72) == "1m 12s"
