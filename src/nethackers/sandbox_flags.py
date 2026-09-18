"""The container hardening flag-sets every box shares (spec §3b). Two variants:
`offline_flags` (arena, plain runs) is fully sealed — no network at all;
`online_flags` (mutator only) keeps the caps but leaves network to the caller's
egress allowlist. Pure argv builders: no docker call, no mounts, no image."""
from __future__ import annotations

_NONROOT = "65534:65534"   # nobody:nogroup — present in slim images

def _caps(*, memory: str, cpus: str, pids: int) -> list[str]:
    return [
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids),
        "--memory", memory, "--memory-swap", memory,
        "--cpus", cpus,
    ]

def offline_flags(*, memory: str = "4g", cpus: str = "2", pids: int = 256,
                  tmpfs_size: str = "512m", user: str = _NONROOT) -> list[str]:
    return [
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}",
        *_caps(memory=memory, cpus=cpus, pids=pids),
        "--user", user,
    ]

def online_flags(*, memory: str = "8g", cpus: str = "4", pids: int = 512) -> list[str]:
    # No --network here: the mutator caller restricts egress to the broker.
    # No --user: the mutator entrypoint (gosu) remaps to the workspace owner.
    return _caps(memory=memory, cpus=cpus, pids=pids)
