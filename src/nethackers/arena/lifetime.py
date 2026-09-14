"""Make the kernel, not our teardown path, responsible for reaping children.

An arena run is a two-level process tree: ``run_batch`` fans episodes out to
pool workers, and each worker puts the submitted bot in a sandbox process of
its own. Both levels are torn down explicitly -- ``with executor:`` and
``run_trajectory``'s ``finally: client.close()`` -- but explicit teardown only
runs when the driver exits cleanly. A driver that is SIGKILLed, OOM-killed or
segfaults skips it, and its children do not find out on their own:

* a pool worker part-way through an episode is running, not waiting on the
  call queue, so the closing pipe never reaches it; afterwards it blocks
  forever on a ``multiprocessing`` queue lock whose holder is gone;
* a bot inside ``act()`` is not reading its connection, so it cannot see EOF
  either. (A bot blocked in ``recv()`` *does* exit by itself -- which is why
  only the first kind was ever observed in the wild.)

Measured on a real evolution run: 450 orphaned workers, ~41 MB each, 17 GB
between them. The cost was not the memory. They starved the *live* run's own
episode startup until it exceeded the harness hang-guard, so clean candidates
came back scored as failures -- a leak that corrupts results, not just RAM.

``PR_SET_PDEATHSIG`` closes this off at the only layer that survives a
SIGKILL. It is Linux-only and best-effort by design: everywhere else this is a
no-op and teardown behaves exactly as before.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from contextlib import suppress

# <linux/prctl.h>. Not exposed by the stdlib before 3.14's os.PR_SET_PDEATHSIG.
_PR_SET_PDEATHSIG = 1


def die_with_parent(parent_pid: int | None = None) -> None:
    """Ask the kernel to SIGKILL this process once its parent goes away.

    Call from inside the child (a pool worker's ``initializer``, a sandbox
    process's entry point) -- never from the parent, which would sign its own
    death warrant.

    ``parent_pid`` closes the startup race: the setting only takes effect from
    the moment it is made, so a parent that died in the window between spawn
    and this call would never deliver the signal. Passing the expected parent
    lets the child notice it has already been orphaned and leave. It is
    checked against a *specific* pid rather than against 1 on purpose: the
    arena image runs its entrypoint as PID 1, where every worker legitimately
    has ``getppid() == 1``.

    The signal is tied to the *thread* that spawned this process, not to the
    parent process as a whole. Both callers spawn from their main thread, so
    the distinction does not bite here; a future caller that spawns from a
    worker thread would see its children reaped when that thread exits.
    """
    if parent_pid is not None and os.getpid() == parent_pid:
        # We *are* the parent: this is a thread pool wearing an executor's
        # interface, not a process pool, so the "child" is a thread in the
        # driver itself (run_batch takes executor_factory precisely so
        # tests can inject one). Arming PDEATHSIG here would set the driver up
        # to be killed by its own parent's exit, and the orphan check below
        # would compare the driver's ppid against its pid and _exit(1) on the
        # spot. There is nothing to guard: a thread cannot outlive us.
        return
    if sys.platform == "linux":
        with suppress(OSError, AttributeError):
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(
                _PR_SET_PDEATHSIG, signal.SIGKILL
            )
    if parent_pid is not None and os.getppid() != parent_pid:
        # Already orphaned; no parent death is coming to trigger the signal.
        os._exit(1)
