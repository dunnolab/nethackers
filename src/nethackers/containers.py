"""Names + a label for every container nethackers starts, so
`docker ps -f name=nethackers-` (and `-f label=nethackers`) list them and
cleanup is one command. See spec §5.13 / D14 / INV13. Leaf module: stdlib only.

Also home to ``container_runtime`` -- the single place that decides WHICH CLI
binary (``docker`` or ``podman``) every container command shells out to. It
lives here (a leaf both ``eval`` and ``harness`` already import) rather than in
``sandbox_preflight`` so the actual ``docker run`` sites can share it without a
new import cycle."""
from __future__ import annotations

import secrets
import shutil
import subprocess
from dataclasses import dataclass

NETHACKERS_LABEL = "nethackers"

# Preference order: an explicit `docker` wins (a user who has both almost
# certainly means Docker), then rootless-friendly `podman`. Both are drop-in
# argv-compatible for everything nethackers runs (`run`/`pull`/`image
# inspect`/`manifest inspect`/`info`), so the resolved name is spliced in as
# argv[0] verbatim -- see the call sites in eval/runner.py, sandbox_preflight.py
# and diagnostics.py.
_RUNTIME_CANDIDATES = ("docker", "podman")


@dataclass(frozen=True)
class RuntimeCandidate:
    """One container CLI's probe result. ``state`` is ``"usable"`` (on PATH and
    ``<exe> info`` exited 0), ``"absent"`` (not on PATH), or ``"broken"`` (on
    PATH but ``info`` failed -- daemon down, socket permission denied, rootless
    unconfigured, timed out). ``detail`` carries the ``info`` failure for
    ``"broken"`` (the line ``doctor`` shows the user), and is ``""`` otherwise."""

    exe: str
    state: str
    detail: str


@dataclass(frozen=True)
class RuntimeReport:
    """The full outcome of probing every candidate. ``runtime`` is the first
    usable exe (what commands actually shell out to) or ``None``; ``candidates``
    is the per-CLI breakdown so ``doctor`` can explain WHY none was usable
    rather than a bare 'no runtime' (issue #50)."""

    runtime: str | None
    candidates: tuple[RuntimeCandidate, ...]


def _info_failure(proc: object) -> str:
    """A one-line reason a ``<exe> info`` call failed, for display. Prefers the
    last non-empty stderr line (where docker/podman put 'permission denied …
    daemon socket', 'Cannot connect to the Docker daemon', etc.), then stdout,
    then a bare exit code. Reads attributes defensively so a returncode-only
    fake ``run`` result never raises here."""
    text = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")
    line = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return line or f"`info` exited {getattr(proc, 'returncode', '?')}"


def probe_container_runtime(*, run=subprocess.run) -> RuntimeReport:
    """Probe each candidate CLI and report which (if any) is usable, plus WHY
    the others aren't. "Usable" = on ``PATH`` (``shutil.which``) AND ``<exe>
    info`` exits 0 -- a binary with nothing behind it (stopped Colima VM,
    unconfigured rootless, no socket permission) is installed but not usable,
    the distinction ``docker info`` always drew (issue #50). A ``"broken"``
    candidate never shadows a later usable one -- ``docker`` failing falls
    through to a working ``podman``.

    Deliberately NOT memoized: called a small, bounded number of times per
    command (a handful in ``doctor``; once per ``eval``/iteration on the hot
    path, which threads the resolved name down), each probe a fast ``info``
    call -- a process cache would just add shared mutable state for no real
    saving. ``run`` is injectable so tests never shell out. NOTE: a shell
    ``alias docker=podman`` is invisible here -- aliases are a shell construct,
    never on ``PATH`` -- which is why detecting ``podman`` directly, not
    relying on an alias, is the fix."""
    candidates: list[RuntimeCandidate] = []
    runtime: str | None = None
    for exe in _RUNTIME_CANDIDATES:
        if shutil.which(exe) is None:
            candidates.append(RuntimeCandidate(exe, "absent", ""))
            continue
        try:
            proc = run([exe, "info"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            candidates.append(RuntimeCandidate(exe, "broken", str(exc)))
            continue
        if getattr(proc, "returncode", 1) == 0:
            candidates.append(RuntimeCandidate(exe, "usable", ""))
            if runtime is None:
                runtime = exe
        else:
            candidates.append(RuntimeCandidate(exe, "broken", _info_failure(proc)))
    return RuntimeReport(runtime, tuple(candidates))


def container_runtime(*, run=subprocess.run) -> str | None:
    """The name of the first usable container runtime binary (``"docker"`` /
    ``"podman"``), or ``None``. The bare-name accessor over
    ``probe_container_runtime`` for the call sites that only need to know what
    to exec; ``doctor`` uses the full report for its per-CLI diagnosis."""
    return probe_container_runtime(run=run).runtime


def container_name(role: str) -> str:
    """`nethackers-<role>-<hex>` — the hex keeps it unique among running
    containers (evals/probes run concurrently; a `--name` must be unique)."""
    return f"nethackers-{role}-{secrets.token_hex(4)}"


def label_args() -> list[str]:
    """The `docker run` args that stamp the shared `nethackers` label."""
    return ["--label", NETHACKERS_LABEL]
