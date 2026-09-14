import subprocess
import time

from nethackers import clipboard


def test_copy_success_uses_first_available_tool():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[0])
        return None

    assert clipboard.copy("hi", run=run) is True
    assert seen == ["pbcopy"]  # first candidate wins


def test_copy_falls_through_when_a_tool_is_missing():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[0])
        if cmd[0] in ("pbcopy", "wl-copy"):
            raise FileNotFoundError
        return None

    assert clipboard.copy("hi", run=run) is True
    assert seen == ["pbcopy", "wl-copy", "xclip"]  # tried in order until one works


def test_copy_returns_false_when_no_tool_works():
    def run(cmd, **kw):
        raise FileNotFoundError

    assert clipboard.copy("hi", run=run) is False


def test_copy_passes_text_via_stdin():
    got = {}

    def run(cmd, **kw):
        got.update(kw)
        return None

    clipboard.copy("WDJB-MJHT", run=run)
    assert got["input"] == "WDJB-MJHT"
    assert got["timeout"] == clipboard._COPY_TIMEOUT


def test_copy_treats_tool_failure_as_fall_through():
    def run(cmd, **kw):
        if cmd[0] == "pbcopy":
            raise subprocess.CalledProcessError(1, cmd)
        return None

    assert clipboard.copy("hi", run=run) is True


def test_copy_treats_hung_tool_as_fall_through():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[0])
        if cmd[0] == "pbcopy":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return None

    assert clipboard.copy("hi", run=run) is True
    assert seen == ["pbcopy", "wl-copy"]


def test_copy_does_not_wait_for_a_forking_helper(tmp_path, monkeypatch):
    """xclip, xsel and wl-copy fork a selection-owning child that outlives the
    copy and inherits the parent's stdout/stderr.  Capturing those pipes makes
    ``subprocess.run`` wait on a process that will not exit, so a copy that
    actually worked looks like a hang and is then reported as a failure."""
    helper = tmp_path / "forking-helper"
    helper.write_text("#!/bin/sh\ncat > /dev/null\n( sleep 5 ) &\nexit 0\n")
    helper.chmod(0o755)
    monkeypatch.setattr(clipboard, "_CANDIDATES", ((str(helper),),))

    started = time.monotonic()
    assert clipboard.copy("WDJB-MJHT") is True
    assert time.monotonic() - started < clipboard._COPY_TIMEOUT
