"""The container hardening flag-sets every box shares (spec §3b). Two variants:
`offline_flags` (arena, plain runs) is fully sealed — no network at all;
`online_flags` (mutator only) keeps the caps but leaves network to the caller's
egress allowlist. Pure argv builders: no docker call, no mounts, no image.
`offline_flags` has no default size: its caps are the caller's to size from
the workload the box will run (see eval/runner.py)."""
from __future__ import annotations

_NONROOT = "65534:65534"   # nobody:nogroup — present in slim images
_CFS_PERIOD_US = 100_000   # the period `--cpus` itself uses

def _caps(*, memory: str, cpus: float, pids: int) -> list[str]:
    return [
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids),
        "--memory", memory, "--memory-swap", memory,
        # The CFS pair `--cpus` expands to, spelled out: docker refuses a
        # `--cpus` above the daemon's CPU count ("Range of CPUs is from 0.01
        # to 4.00, as there are only 4 CPUs available"), and a box sized from
        # its workload can ask for more cores than a small host has. A quota
        # past the host's cores is accepted and simply never binds.
        "--cpu-period", str(_CFS_PERIOD_US),
        "--cpu-quota", str(round(cpus * _CFS_PERIOD_US)),
    ]

def offline_flags(*, memory: str, cpus: float, pids: int,
                  tmpfs_size: str = "512m", user: str = _NONROOT) -> list[str]:
    return [
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}",
        *_caps(memory=memory, cpus=cpus, pids=pids),
        "--user", user,
    ]

def online_flags(*, memory: str = "8g", cpus: float = 4, pids: int = 512) -> list[str]:
    # No --network here: the mutator caller restricts egress to the broker.
    # No --user: the mutator entrypoint (gosu) remaps to the workspace owner.
    return _caps(memory=memory, cpus=cpus, pids=pids)
