"""Run a command in a pseudo-terminal and read what it prints as lines.

Tools print their live progress -- docker's byte counts, curl's percentage,
brew's bars -- only when attached to a terminal, and they redraw it in place
with carriage returns and cursor moves. ``spawn`` gives the child a pty for
stdout and stderr (stdin gets nothing, so a surprise prompt fails fast instead
of hanging); ``read_lines`` turns the redraw stream into plain status lines:
colors dropped, and every cursor move, ``\\r`` or ``\\n`` ending a line.

The child stays in our process group, so Ctrl-C reaches it too; ``stop``
makes sure it is gone afterwards. POSIX only -- setup doesn't cover native
Windows. Leaf module: stdlib only.
"""
from __future__ import annotations

import codecs
import errno
import os
import re
import select
import struct
import subprocess
from collections.abc import Callable, Iterator, Sequence
from typing import Any

try:  # POSIX only
    import fcntl
    import pty
    import termios
except ImportError:  # pragma: no cover - native Windows
    pty = None  # type: ignore[assignment]

# Wide enough that docker never truncates its progress lines.
WIDTH = 200

_SGR = re.compile(r"\x1b\[[0-9;]*m")                    # colors: dropped
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # titles/hyperlinks: dropped
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")        # cursor moves/clears: line breaks
_ESC = re.compile(r"\x1b[@-Z\\-_]")                    # other escapes: dropped
_BREAK = re.compile(r"[\r\n]")


def available() -> bool:
    return pty is not None


def clean(text: str) -> list[str]:
    """Raw terminal output as the lines a person would have seen."""
    text = _OSC.sub("", _SGR.sub("", text))
    text = _ESC.sub("", _CSI.sub("\n", text))
    return [piece.strip() for piece in _BREAK.split(text) if piece.strip()]


class LineSplitter:
    """Feed raw text in, get the complete lines out; an unfinished line (or an
    escape sequence cut by a read) waits for the next feed."""

    def __init__(self) -> None:
        self._tail = ""

    def feed(self, text: str) -> list[str]:
        text = self._tail + text
        cut = max(text.rfind("\r"), text.rfind("\n"))
        if cut < 0:
            self._tail = text
            return []
        self._tail = text[cut + 1:]
        return clean(text[: cut + 1])

    def flush(self, text: str = "") -> list[str]:
        rest, self._tail = self._tail + text, ""
        return clean(rest)


def spawn(argv: Sequence[str], *, env: dict[str, str] | None = None,
          popen: Callable[..., Any] = subprocess.Popen) -> tuple[Any, int]:
    """Start ``argv`` with a pty on stdout/stderr and nothing on stdin; return
    the process and the pty's reading end (closed by ``read_lines``)."""
    if pty is None:
        raise OSError("pseudo-terminals are not available on this platform")
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, WIDTH, 0, 0))
        proc = popen(list(argv), stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                     env=env, close_fds=True)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    return proc, master


def read_lines(master: int, *, done: Callable[[], bool] = lambda: False,
               timeout: float = 0.1) -> Iterator[list[str] | None]:
    """Yield each batch of complete lines as they are printed, and ``None``
    after every ``timeout`` seconds of silence (so a caller can redraw a
    timer). Stops at end of output, or at the first silence after ``done()``
    turns true -- a background process the child left behind can hold the pty
    open forever. Closes ``master``."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    splitter = LineSplitter()
    try:
        while True:
            ready, _, _ = select.select([master], [], [], timeout)
            if not ready:
                if done():
                    break
                yield None
                continue
            try:
                data = os.read(master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:  # Linux: every writer closed the pty
                    break
                raise
            if not data:  # macOS: end of output
                break
            batch = splitter.feed(decoder.decode(data))
            if batch:
                yield batch
        rest = splitter.flush(decoder.decode(b"", final=True))
        if rest:
            yield rest
    finally:
        os.close(master)


def stop(proc: Any, *, grace: float = 5.0) -> None:
    """Make sure a child is gone after Ctrl-C: it got the SIGINT too (same
    process group), so wait for it first, then terminate, then kill."""
    for send in (lambda: None, proc.terminate, proc.kill):
        try:
            send()
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
