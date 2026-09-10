"""No arena child process may outlive the process that started it.

Teardown alone cannot provide that guarantee. ``run_batch``'s ``with
executor:`` and ``run_trajectory``'s ``finally: client.close()`` both run only
when the driver exits *cleanly*; a driver that is SIGKILLed, OOM-killed or
segfaults skips them entirely. Its children are then reparented to init, and
they do not notice: a pool worker part-way through an episode is holding an
NLE game and its BLAS/OpenMP thread pools and simply keeps running, and once
its ``multiprocessing`` queue lock's holder is gone it blocks on that futex
forever.

Measured on a real evolution run: 450 such orphans, ~41 MB each, 17 GB
between them. They did not merely waste memory -- they starved the *live*
run's own episode startup until it exceeded the harness hang-guard, so clean
candidates came back scored as failures. A leak that corrupts selection is
worse than one that only costs RAM.

Both tests kill the driver the way the kernel does -- SIGKILL, no atexit, no
``finally`` -- and assert the children are gone shortly after. They need no
NLE: the episode body and the bot are fixtures.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
GRACE_SECONDS = 5.0


def _alive(pid: int) -> bool:
    """True while ``pid`` exists. The child is reparented to init once its
    driver dies, so it is no longer waitable -- signal 0 is the only probe
    left, and it still works because the orphan keeps our uid."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pids, timeout=GRACE_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(_alive(pid) for pid in pids)


def _reap(pids):
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def test_batch_workers_die_with_a_killed_driver():
    """The observed leak: episodes in flight when the driver is killed.

    Each worker is mid-``run_one`` -- not blocked on the call queue -- so it
    never sees the parent's pipe close. Nothing but the kernel will stop it.
    """
    driver = subprocess.Popen(
        [sys.executable, str(FIX / "orphan_batch_driver.py")],
        stdout=subprocess.PIPE, text=True,
    )
    workers = []
    try:
        for _ in range(2):
            workers.append(int(driver.stdout.readline()))
        assert all(_alive(pid) for pid in workers), "fixture never started its episodes"

        driver.kill()
        driver.wait(timeout=10)

        assert _wait_gone(workers), (
            f"workers {workers} outlived their driver -- each still holds an "
            f"episode's memory and thread pools"
        )
    finally:
        _reap(workers)
        if driver.poll() is None:
            driver.kill()


def test_sandboxed_bot_dies_with_a_killed_driver():
    """The second-order leak: a bot that is *computing* when its driver dies.

    A bot blocked in ``connection.recv()`` does notice -- the closing pipe
    gives it EOF and it exits on its own, which is why no orphan of that kind
    was ever observed. A bot inside ``act()`` is not reading the connection at
    all, so EOF cannot reach it; ``wait_bot`` stands in for the real case, an
    agent still thinking about its move.
    """
    driver = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys\n"
            "from pathlib import Path\n"
            "from nethackers.arena.sandbox import AgentClient\n"
            "obs = {'blstats': [0] * 27}\n"
            "c = AgentClient(Path(sys.argv[1]), bot_seed=0, action_count=8,\n"
            "                timeout_seconds=60.0)\n"
            # No public accessor for the child pid; the test needs it to prove
            # the process is gone, and only the sandbox knows it.
            "print(c._process.pid, flush=True)\n"
            "c.reset(obs)\n"
            "c.act(obs)\n",
            str(FIX / "bots" / "wait_bot"),
        ],
        stdout=subprocess.PIPE, text=True,
    )
    bot = None
    try:
        bot = int(driver.stdout.readline())
        time.sleep(0.5)  # let the driver get into act(), so the bot is sleeping
        assert _alive(bot)

        driver.kill()
        driver.wait(timeout=10)

        # wait_bot sleeps 10s inside act(); the grace window is well inside
        # that, so a pass cannot be the bot merely finishing on its own.
        assert _wait_gone([bot]), f"bot {bot} outlived its driver"
    finally:
        if bot is not None:
            _reap([bot])
        if driver.poll() is None:
            driver.kill()
