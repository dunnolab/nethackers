"""A hard wall-clock guard for a blocking call the socket/httpx timeout can't
bound. httpx's own timeout covers connect/read, but NOT DNS resolution:
CPython's ``socket.create_connection`` runs ``getaddrinfo`` *before* it applies
any socket timeout, so a hung resolver hangs the whole call forever -- and
un-interruptibly, since a single Ctrl-C can't land while the interpreter is
blocked in a C-level ``getaddrinfo`` (issue #50).

``call_with_deadline`` runs the call on a daemon thread and gives up after the
deadline, turning that hang into a fast, catchable ``TimeoutError``. It cannot
*cancel* the underlying call (nothing can interrupt ``getaddrinfo`` from
outside), so the abandoned thread lingers -- harmless for the short-lived CLI
that owns these calls: it dies with the process. Use it only for genuinely
bounded-lifetime work (a single network round-trip), never for something whose
side effects must complete."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Wall-clock ceiling for one network round-trip that must not hang. Set a few
# seconds ABOVE httpx's own 5s connect/read default so a normal connection
# failure still surfaces with its precise httpx cause (ConnectError/timeout),
# and this ceiling only ever trips on a hung DNS lookup that the socket timeout
# can't bound (issue #50) -- at which point 8s is long enough not to false-trip
# a merely-slow resolver, short enough that `login`/`doctor` fail fast.
DEADLINE = 8.0


def call_with_deadline(fn: Callable[[], T], seconds: float) -> T:
    """Return ``fn()``'s result, or raise ``TimeoutError`` if it hasn't
    finished within ``seconds``.

    ``fn`` runs on a daemon thread; an exception it raises propagates to the
    caller unchanged (with its original traceback). On timeout the thread is
    abandoned -- see the module docstring for why that's acceptable here and
    where it is not."""
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # capture, re-raise on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"call did not complete within {seconds}s")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]
