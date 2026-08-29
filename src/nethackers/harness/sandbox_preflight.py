"""Preflight + acquisition for the mandatory arena/mutator sandboxes, shared by
the CLI and the TUI.

Everything here runs in a container -- there is no host-execution path -- so
before a run starts we verify a working container runtime, acquire the image,
and (mutator only) a resolvable host login for the selected harness, failing
fast with one clear, styled message instead of a mid-run crash. Every
``str | None``-returning function here follows the same contract: ``None``
means "good to go", else a Rich-markup string to display (CLI stderr / TUI
``#f_err`` line).

Two separate gates (spec S5.5) -- do NOT fuse them back together:
``preflight_runtime`` (a working container runtime) is needed by every
sandboxed command, including a plain ``eval``/``submit``; ``preflight_operator``
(a resolvable ``codex``/``claude`` host login) is needed ONLY by ``evolve`` --
the arena has no operator, so eval/submit must never call it. ``preflight``
stays as a thin backward-compatible wrapper over both, for evolve's use.

``ensure_image`` makes a ``resolve_image``-produced ref actually present:
builds the local ``:dev``/``:latest`` tag via ``make`` inside a repo checkout,
or ``docker pull``s a GHCR digest pin otherwise -- never the reverse (INV11:
a digest ref can't be `-t`-tagged by a build, so it is only ever pulled).

Kept a leaf module (stdlib + auth_inject only) so both ``cli`` and the
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


_MAKE_VAR = {"arena": "ARENA_IMAGE", "mutator": "MUTATOR_IMAGE"}
_DOCKERFILE = {"arena": "arena/Dockerfile", "mutator": "Dockerfile.mutator"}
_ENV_VAR = {"arena": "NETHACKERS_ARENA_IMAGE", "mutator": "NETHACKERS_MUTATOR_IMAGE"}


def _build_image(image: str, kind: str, *, on_line=None, popen=subprocess.Popen,
                 repo_root=_repo_root) -> str | None:
    """``make {kind} {KIND}_IMAGE={image}`` from the repo root, streaming each
    build line to ``on_line``. ``None`` on success, else a styled error. Never
    called on a digest ref (INV11) -- ``ensure_image`` guards that; this helper
    just runs the build it's told to. ``repo_root`` injectable so a caller that
    already decided "we're in a repo" (``ensure_image``) builds from the exact
    root it checked, rather than re-deriving it from a second, independent
    ``_repo_root()`` call."""
    root = repo_root()
    if root is None:
        return (f"[red]can't set up the sandbox[/]: run nethackers from its repo "
                f"(the {kind} image builds from {_DOCKERFILE[kind]} there)")
    try:
        proc = popen(["make", kind, f"{_MAKE_VAR[kind]}={image}"], cwd=str(root),
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


def build_mutator_image(image: str, *, on_line=None, popen=subprocess.Popen) -> str | None:
    """Build the mutator sandbox image (``make mutator`` -> nle-base + mutator),
    streaming each build line to ``on_line``. ``None`` on success, else a styled
    error. The mutator ALWAYS runs sandboxed, so the very first run auto-provisions
    the image here (users never run ``make`` themselves) -- the only cost is the
    one-time NLE compile. A thin back-compat wrapper over ``_build_image``; new
    code should call ``ensure_image(image, "mutator", ...)`` instead, which also
    covers the pulled/pinned case."""
    return _build_image(image, "mutator", on_line=on_line, popen=popen)


def _pull_image(ref: str, kind: str, *, on_line=None, popen=subprocess.Popen) -> str | None:
    """``docker pull ref``, streaming combined stdout/stderr to ``on_line``.
    ``None`` on success, else one of the styled, one-runnable-command messages
    (spec S5.5 / INV9) mapped from the registry's failure shape."""
    try:
        proc = popen(["docker", "pull", ref], stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        collected: list[str] = []
        for line in proc.stdout:
            collected.append(line)
            if on_line is not None:
                on_line(line.rstrip())
        rc = proc.wait()
    except (OSError, subprocess.SubprocessError) as exc:
        return _pull_error_message(kind, str(exc))
    if rc != 0:
        return _pull_error_message(kind, "".join(collected))
    return None


def _pull_error_message(kind: str, text: str) -> str:
    """Map a failed ``docker pull``'s output (or the exception raised trying
    to run it) to exactly one thing to do next (INV9) -- never a raw registry
    error or traceback. Checked most-specific-first: a missing manifest (never
    published, or a typo'd ref) before a bare "not found", since GHCR's auth-
    or-missing wording overlaps."""
    low = text.lower()
    if "manifest unknown" in low or "not found" in low or "404" in low:
        return (f"[red]no published sandbox for this build[/] — clone the repo, "
                f"or set `{_ENV_VAR[kind]}`")
    if "denied" in low or "unauthorized" in low or "403" in low:
        return "[red]stale ghcr login[/] — run `docker logout ghcr.io`, then retry"
    return ("[red]couldn't reach the registry[/] — only the first run needs the "
           "network; a pulled image keeps working, so retry once you're online")


def ensure_image(ref: str, kind: str, *, on_line=None, popen=subprocess.Popen,
                 run=subprocess.run, repo_root=_repo_root) -> str | None:
    """Make the already-resolved ``ref`` (``resolve_image``'s output -- INV2,
    never a raw config field) available locally, acquiring it if not. ``kind``
    is ``"arena"``/``"mutator"``. ``None`` on success, else a styled error
    ending in exactly one runnable command (INV9).

    Branches, checked in this order:
      1. already present locally (cheap ``image_present``) -> nothing to do.
      2. a GHCR digest pin (``@sha256:`` in ``ref``) -> ``docker pull`` it,
         mapping the registry's failure shapes to one-command messages. This
         check comes BEFORE the repo-checkout check -- INV11 ("never build a
         digest ref") is a hard safety rule, so it wins even in the (today
         unreachable via ``resolve_image``'s own ladder, but not impossible if
         someone hand-sets an env override) case of a repo checkout with an
         explicit pinned ref.
      3. else, inside a nethackers checkout (``_repo_root``) -> ``make {kind}``
         builds it -- the local dev tag, or any other non-digest tag asked for.
      4. else (a custom non-digest ref with no repo to build it from) ->
         best-effort ``docker pull`` -- it's the caller's own registry ref.
    """
    if image_present(ref, run=run):
        return None
    if "@sha256:" in ref:
        return _pull_image(ref, kind, on_line=on_line, popen=popen)
    if repo_root() is not None:
        return _build_image(ref, kind, on_line=on_line, popen=popen, repo_root=repo_root)
    return _pull_image(ref, kind, on_line=on_line, popen=popen)


def preflight_runtime(*, run=subprocess.run) -> str | None:
    """``None`` if a working container runtime is available; else the styled
    "no container runtime" message. The half of the old combined ``preflight``
    every sandboxed command needs -- ``eval``/``submit``/``evolve`` all score
    or mutate inside a container -- so this alone is the correct (and only)
    preflight for a plain ``eval``/``submit`` (spec S5.5: arena has no
    operator)."""
    if not docker_available(run=run):
        return (
            f"[red]sandbox unavailable[/]: no working container runtime found "
            f"— {sandbox_hint()}, then retry"
        )
    return None


def preflight_operator(operator: str, *, system: str | None = None,
                       home: Path | None = None) -> str | None:
    """``None`` if a resolvable host login exists for ``operator`` (the
    token/creds the mutator container reuses); else the styled "not logged
    in" message. ``evolve``-only: the arena has no operator, so a plain
    ``eval``/``submit`` must NEVER call this (spec S5.5's "two separate
    gates" -- fusing them back together is exactly the bug this split
    fixes)."""
    try:
        auth_docker_args(operator, system=system or platform.system(),
                         home=home or Path.home(), _require_exists=True)
    except AuthUnavailable as exc:
        return f"[red]not logged in[/]: {exc.hint}"
    return None


def preflight(operator: str, *, system: str | None = None,
              home: Path | None = None, run=subprocess.run) -> str | None:
    """``None`` if the mutator sandbox can run; else a styled, human-facing
    error. Backward-compatible combined gate -- ``preflight_runtime`` then
    ``preflight_operator`` -- kept for evolve's call sites (which need both).
    New code that only needs the runtime half (``eval``/``submit``) should
    call ``preflight_runtime`` directly rather than this. The image itself is
    auto-provisioned (``ensure_image``), not a precondition checked here."""
    msg = preflight_runtime(run=run)
    if msg is not None:
        return msg
    return preflight_operator(operator, system=system, home=home)
