"""Preflight for the mandatory mutator sandbox, shared by the CLI and the TUI.

The mutator always runs in a container -- there is no host-execution path -- so
before a run starts we verify a working container runtime AND a resolvable host
login for the selected harness, failing fast with one clear, styled message
instead of a mid-run crash. ``preflight`` returns a Rich-markup string to
display (CLI stderr / TUI ``#f_err`` line), or ``None`` when the sandbox is good
to go. Kept a leaf module (stdlib + auth_inject only) so both ``cli`` and the
Textual form can import it without a cycle.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from nethackers.harness.auth_inject import AuthUnavailable, auth_docker_args


def sandbox_hint() -> str:
    """How to bring a container runtime up, per-OS. macOS has no native Docker
    daemon (Docker Desktop is out per the mutator-sandbox spec) -- its fix is a
    VM, not just "start Docker"."""
    if platform.system() == "Darwin":
        return (
            "start one, e.g. `colima start --cpu 6 --memory 12 --vm-type vz "
            "--mount-type virtiofs` (or Podman)"
        )
    return "start Docker or Podman"


def docker_available(*, run=subprocess.run) -> bool:
    """A ``docker`` binary on PATH can still have no daemon behind it -- a
    stopped Colima VM looks exactly like this -- so ``docker info`` is what
    proves the runtime is usable, not just installed."""
    if shutil.which("docker") is None:
        return False
    try:
        # Generous but bounded: covers a slow-to-answer Colima VM without
        # hanging indefinitely if the runtime is just gone.
        result = run(["docker", "info"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def preflight(operator: str, *, system: str | None = None,
              home: Path | None = None, run=subprocess.run) -> str | None:
    """``None`` if the mutator sandbox can run; else a styled, human-facing
    error. Two checks, in order: a working container runtime, then a resolvable
    host login for ``operator`` (the token/creds the container reuses)."""
    if not docker_available(run=run):
        return (
            f"[red]sandbox unavailable[/]: no working container runtime found "
            f"— {sandbox_hint()}, then retry"
        )
    try:
        auth_docker_args(operator, system=system or platform.system(),
                         home=home or Path.home(), _require_exists=True)
    except AuthUnavailable as exc:
        return f"[red]not logged in[/]: {exc.hint}"
    return None
