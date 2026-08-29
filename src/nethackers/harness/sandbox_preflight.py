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

from nethackers import _image_pins
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


def image_present(image: str, *, docker: str = "docker", run=subprocess.run) -> bool:
    """Is the mutator image available locally? Cheap ``docker image inspect`` (no
    pull). Used to decide whether to auto-build it (``build_mutator_image``) and
    by discovery, so an unbuilt image degrades quietly instead of silently
    reporting the host CLI's models."""
    try:
        return run([docker, "image", "inspect", image],
                   capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _repo_root() -> Path | None:
    """The nethackers repo checkout (the dir holding ``Dockerfile.mutator`` +
    ``Makefile``), searched from CWD upward -- the build context the sandbox
    image is built from. ``None`` when nethackers isn't being run from its repo
    (then the image can't be auto-built and must be pulled/built out of band)."""
    for base in (Path.cwd(), *Path.cwd().parents):
        if (base / "Dockerfile.mutator").is_file() and (base / "Makefile").is_file():
            return base
    return None


_LOCAL_DEV_REF = {"arena": "nethackers/arena:dev", "mutator": "nethackers/mutator:latest"}
_PIN = {"arena": _image_pins.ARENA_IMAGE, "mutator": _image_pins.MUTATOR_IMAGE}


def resolve_image(explicit: str | None, kind: str, *, repo_root=_repo_root) -> str:
    """The image ref to use for ``kind`` (``"arena"``/``"mutator"``). Ladder
    (spec §5.1): an explicit value (flag / env / .env.stack — anything that made
    the layered Stage field non-None) wins verbatim; else a repo checkout uses the
    locally-built dev tag; else the pinned GHCR digest. NO side effects — never
    builds or pulls (safe in EvolveParams default factories); building/pulling
    happens at the acquisition points. ``repo_root`` injectable for tests."""
    if explicit is not None:
        return explicit
    if repo_root() is not None:
        return _LOCAL_DEV_REF[kind]
    return _PIN[kind]


def build_mutator_image(image: str, *, on_line=None, popen=subprocess.Popen) -> str | None:
    """Build the mutator sandbox image (``make mutator`` -> nle-base + mutator),
    streaming each build line to ``on_line``. ``None`` on success, else a styled
    error. The mutator ALWAYS runs sandboxed, so the very first run auto-provisions
    the image here (users never run ``make`` themselves) -- the only cost is the
    one-time NLE compile."""
    root = _repo_root()
    if root is None:
        return ("[red]can't set up the sandbox[/]: run nethackers from its repo "
                "(the sandbox image builds from Dockerfile.mutator there)")
    try:
        proc = popen(["make", "mutator", f"MUTATOR_IMAGE={image}"], cwd=str(root),
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            if on_line is not None:
                on_line(line.rstrip())
        rc = proc.wait()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[red]sandbox setup failed[/]: {exc}"
    if rc != 0:
        return "[red]sandbox setup failed[/] — the build did not complete (see the log above)"
    return None


def preflight(operator: str, *, system: str | None = None,
              home: Path | None = None, run=subprocess.run) -> str | None:
    """``None`` if the mutator sandbox can run; else a styled, human-facing
    error. Two checks, in order: a working container runtime, then a resolvable
    host login for ``operator`` (the token/creds the container reuses). The
    image itself is auto-built on demand (``build_mutator_image``), not a
    precondition the user must satisfy."""
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
