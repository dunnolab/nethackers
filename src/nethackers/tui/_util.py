"""Shared helpers used by tui screens. Originally copied -- not imported --
from tui.app.EvolveApp's module-level definitions per Ruling R-util:
screens must not import from tui.app, since (as of Task 10's NetHackersApp
shell cutover) tui.app imports the screens defined here; `tui.app ->
tui._util` would become a cycle if screens imported the other direction.

``failure_detail`` (+ its two small helpers) moved here from tui.app (Ruling
8, task-6 fix round 1): it is pure -- no Textual -- so tui/story.py (also
pure) can use it for the run-failed now-line without importing tui.app,
which would drag in Textual. tui.app re-imports it so `from
nethackers.tui.app import failure_detail` keeps working for its own toast
and for existing callers/tests."""
from __future__ import annotations

import functools
import re
import subprocess


def _slug(tag: str) -> str:
    return "log_" + tag.replace(" ", "_").replace("/", "_")


def _rows_in_order(rows_by_index: dict[int, dict]) -> list[dict]:
    """Render a batch's episode rows in index (== batch/seed) order,
    regardless of the order they actually completed in -- parallel eval
    (M3) means episode k+1 can finish before episode k."""
    return [rows_by_index[k] for k in sorted(rows_by_index)]


_TOAST_DETAIL_MAXLEN = 200  # a toast shows a one-line reason; full error lives in the run/log


def _cap(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _TOAST_DETAIL_MAXLEN else s[: _TOAST_DETAIL_MAXLEN - 1] + "…"


_DOCKER_HINT = re.compile(r"^See '.*--help'\.?$")  # docker's trailing "See '… --help'." boilerplate


def failure_detail(error: BaseException) -> str:
    """One-line, human reason for a failed run, safe for a plain-text toast.

    Prefers the tail of a captured subprocess stderr (e.g. Docker's own error
    line), else a compact exit-status line, else the exception text -- never the
    giant CalledProcessError command repr."""
    stderr = getattr(error, "stderr", None)
    if stderr:
        text = stderr.decode() if isinstance(stderr, bytes) else str(stderr)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # docker appends a generic "See 'docker … --help'." hint after the real
        # error line; drop it so the toast shows the actual cause, not the hint.
        lines = [ln for ln in lines if not _DOCKER_HINT.match(ln)] or lines
        if lines:
            return _cap(lines[-1])
    if isinstance(error, subprocess.CalledProcessError):
        return _cap(f"eval exited {error.returncode}")
    return _cap(str(error))


def _guarded(method):
    """Per the M3 evolve TUI design spec's Errors section: "a callback
    raising is caught and dropped (logged to a debug buffer, never
    surfaced)". Without this, an exception raised inside a handler body
    propagates through call_from_thread's future.result() back into the
    worker thread -- aborting the whole evolve run over a display concern
    (malformed episode/state dict, bad log line), including at the
    cold-start/done call sites in run_loop that sit outside its
    per-iteration try/except."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self.log.error(f"display handler {method.__name__} raised: {exc!r}; dropped")
            return None
    return wrapper
