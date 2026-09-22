"""Runs one planned step and reports how it went.

Two ways to run a command: a login gets the whole terminal (the tool asks its
own questions); an install or a start runs in a pseudo-terminal with one live
line -- spinner, elapsed time, the tool's latest output line -- so it never
sits silent, and its last 15 lines are kept for a failure. Image pulls are the
caller's pull function, which draws its own progress bar.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from nethackers import ptyrun

# The same number sandbox_preflight keeps for a failed image build.
TAIL_LINES = 15

# Captured steps can't answer questions: Homebrew and the Codex installer both
# read these to skip theirs.
CAPTURED_ENV = {"NONINTERACTIVE": "1", "CODEX_NON_INTERACTIVE": "1"}


@dataclass(frozen=True)
class StepResult:
    ok: bool
    seconds: float
    detail: str = ""                 # what went wrong, or a short result ("@you", "875 MB")
    tail: tuple[str, ...] = ()       # the command's last lines, kept for a failure
    skipped: bool = False            # not run: a step it needs didn't succeed


def clock_text(seconds: float) -> str:
    """The live timer: 0:21, 1:04."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def elapsed_text(seconds: float) -> str:
    """A finished step's duration: 34 s, 1m 12s."""
    whole = int(round(seconds))
    return f"{whole} s" if whole < 60 else f"{whole // 60}m {whole % 60:02d}s"


def run_captured(
    argv: Sequence[str],
    *,
    title: str,
    console: Console,
    spawn: Callable[..., tuple[Any, int]] = ptyrun.spawn,
    clock: Callable[[], float] = time.monotonic,
    env: dict[str, str] | None = None,
) -> StepResult:
    """Run an install or a start in a pseudo-terminal. On a terminal, one live
    line shows it's alive; either way the last ``TAIL_LINES`` lines are kept.
    Ctrl-C stops the child before propagating."""
    start = clock()
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    proc: Any = None

    try:
        effective_env = env if env is not None else {**os.environ, **CAPTURED_ENV}

        if console.is_terminal:
            spinner = Spinner("dots", text=_live_text(title, 0.0, "", console.width))
            with Live(spinner, console=console, transient=True, refresh_per_second=10):
                try:
                    proc, master = spawn(list(argv), env=effective_env)
                except FileNotFoundError:
                    return StepResult(False, 0.0, f"`{argv[0]}` isn't installed")

                lines = ptyrun.read_lines(master, done=lambda: proc.poll() is not None)
                latest = ""
                for batch in lines:
                    if batch:
                        tail.extend(batch)
                        latest = batch[-1]
                    spinner.update(text=_live_text(title, clock() - start, latest,
                                                   console.width))
        else:
            try:
                proc, master = spawn(list(argv), env=effective_env)
            except FileNotFoundError:
                return StepResult(False, 0.0, f"`{argv[0]}` isn't installed")

            lines = ptyrun.read_lines(master, done=lambda: proc.poll() is not None)
            for batch in lines:
                if batch:
                    tail.extend(batch)

        code = proc.wait()
    except KeyboardInterrupt:
        if proc is not None:
            ptyrun.stop(proc)
        raise
    seconds = clock() - start
    if code == 0:
        return StepResult(True, seconds, tail=tuple(tail))
    return StepResult(False, seconds, f"exited {code}", tuple(tail))


def _live_text(title: str, seconds: float, latest: str, width: int) -> Text:
    text = Text.assemble((title, "bold"), " · ", clock_text(seconds))
    room = width - len(title) - 16
    if latest and room > 10:
        text.append(" · ")
        text.append(latest[:room], style="dim")
    return text


def run_terminal(
    argv: Sequence[str],
    *,
    run: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> StepResult:
    """Run a login in this terminal: the tool asks its own questions."""
    start = clock()
    try:
        code = run(list(argv)).returncode
    except FileNotFoundError:
        return StepResult(False, 0.0, f"`{argv[0]}` isn't installed")
    return StepResult(code == 0, clock() - start, "" if code == 0 else f"exited {code}")
