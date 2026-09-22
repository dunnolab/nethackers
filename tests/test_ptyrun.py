"""Pseudo-terminal spawning and redraw-stream splitting. The spawn tests run
a real local `python` child -- no docker, no network."""
from __future__ import annotations

import sys
import time

import pytest

from nethackers import ptyrun

pytestmark = pytest.mark.skipif(not ptyrun.available(), reason="needs a POSIX pty")


def test_clean_drops_colors_and_splits_on_redraws():
    raw = "\x1b[34m==>\x1b[0m \x1b[1mPouring lima\x1b[0m\r\n12.3%\r45.6%\x1b[2K\rdone\n"
    assert ptyrun.clean(raw) == ["==> Pouring lima", "12.3%", "45.6%", "done"]


def test_clean_turns_docker_cursor_moves_into_line_breaks():
    raw = ("\x1b[1A\x1b[2K\r0a1b2c3d4e5f: Downloading [==>    ]  45.2MB/355MB\r\x1b[1B"
           "\x1b[2K\r1b2c3d4e5f60: Pull complete\r")
    assert ptyrun.clean(raw) == ["0a1b2c3d4e5f: Downloading [==>    ]  45.2MB/355MB",
                                 "1b2c3d4e5f60: Pull complete"]


def test_the_splitter_holds_back_an_unfinished_line():
    splitter = ptyrun.LineSplitter()
    assert splitter.feed("abc") == []
    assert splitter.feed("def\rgh") == ["abcdef"]
    assert splitter.flush("i") == ["ghi"]


def test_the_child_sees_a_terminal_and_its_output_arrives_as_lines():
    proc, master = ptyrun.spawn([sys.executable, "-c",
                                 "import os, sys; print(os.isatty(1)); "
                                 "sys.stdout.write('a\\rb\\n'); sys.stdout.flush()"])
    lines = [ln for batch in ptyrun.read_lines(master, done=lambda: proc.poll() is not None)
             if batch for ln in batch]
    assert proc.wait() == 0
    assert lines == ["True", "a", "b"]


def test_reading_stops_when_the_child_exits_even_if_a_grandchild_keeps_the_pty():
    proc, master = ptyrun.spawn(["sh", "-c", "sleep 5 & echo started"])
    start = time.monotonic()
    lines = [ln for batch in ptyrun.read_lines(master, done=lambda: proc.poll() is not None)
             if batch for ln in batch]
    assert time.monotonic() - start < 3
    assert "started" in lines


def test_stop_ends_a_child_that_ignores_the_first_signal():
    proc, master = ptyrun.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    ptyrun.stop(proc, grace=0.2)
    assert proc.poll() is not None
    list(ptyrun.read_lines(master, done=lambda: True))   # closes the pty
