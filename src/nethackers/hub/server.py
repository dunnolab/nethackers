"""The ``nethackers-hub`` console entrypoint.

A thin argparse + uvicorn launcher over ``create_default_app`` (the
environment-configured FastAPI factory in ``nethackers.hub.api``). ``uvicorn``
and the app factory are imported lazily inside ``main`` so that importing this
module -- and asserting parser defaults in ``tests/hub/test_server.py`` -- never
pulls in uvicorn or opens a database.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# CPU budget as the kernel reports it to *this* container. cgroup v2 states it
# as "<quota> <period>" (or "max <period>"); v1 splits the pair across two
# files and uses -1 for "no limit".
_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _cgroup_cpus() -> float | None:
    """This container's CPU quota in cores, or ``None`` when unlimited/absent."""
    try:
        if _CGROUP_V2.exists():
            quota, period = _CGROUP_V2.read_text().split()
            return None if quota == "max" else int(quota) / int(period)
        if _CGROUP_V1_QUOTA.exists():
            v1_quota = int(_CGROUP_V1_QUOTA.read_text())
            if v1_quota <= 0:
                return None
            return v1_quota / int(_CGROUP_V1_PERIOD.read_text())
    except (OSError, ValueError, ZeroDivisionError):
        return None
    return None


def default_workers() -> int:
    """How many worker processes this machine should run: one per available
    core, resolved at run time so resizing the VM needs no config change.

    Deliberately NOT ``os.cpu_count()`` alone. Inside a container that reports
    the *host's* cores, so under a ``cpus:`` limit it would fork a worker per
    host core into a fraction of one core's budget -- strictly worse than not
    scaling at all. The cgroup quota is the honest number wherever one exists;
    ``os.cpu_count()`` is the bare-metal fallback. Never returns 0."""
    quota = _cgroup_cpus()
    if quota is not None:
        return max(1, int(quota))
    return max(1, os.cpu_count() or 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nethackers-hub")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    # Left as None here, resolved in main(): the default belongs to the machine
    # the server runs on, not the one the parser was imported on.
    p.add_argument("--workers", type=int, default=None,
                   help="worker processes (default: one per available core)")
    return p


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from nethackers.hub.api import create_default_app

    ns = build_parser().parse_args(argv)
    workers = ns.workers if ns.workers is not None else default_workers()
    if workers > 1:
        # uvicorn can only fork workers from an import string, never a live
        # object -- each child imports and calls the factory itself. Each is a
        # separate process with its own GIL and its own (thread-local) sqlite
        # connections; that is how the hub uses more than one core, since
        # threads within one process serialize on the GIL.
        uvicorn.run("nethackers.hub.api:create_default_app", factory=True,
                    host=ns.host, port=ns.port, workers=workers)
    else:
        uvicorn.run(create_default_app, factory=True, host=ns.host, port=ns.port)
