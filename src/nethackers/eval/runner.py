"""Local eval orchestrator: runs a solution through the pinned arena Docker
image against a published ``ObjectiveSpec`` batch and wraps the resulting
``list[TrajectoryResult]`` JSON into an ``Evidence`` record.

This module never talks to Docker directly -- ``eval_batch`` shells out via
an injectable ``runner`` callable (default ``subprocess.run``), so
tests/test_eval_runner_m2a.py exercises the command-building and
result-wrapping logic with a fake runner and never needs a real Docker
daemon or the NLE-backed arena image (that full-stack path is
tests/test_docker_smoke.py, gated behind the ``docker`` marker).

``eval_batch`` runs a published ``ObjectiveSpec`` batch -- a fixed, ordered
``((seed, character), ...)`` list -- as one episode per pair, and records
``evaluator_image`` as the evaluator image's resolved content *digest* (via
the injectable ``image_digest_resolver``, default ``_default_image_digest``)
rather than its mutable tag, so evidence stays attributable to the exact
image bytes that produced it. This is now the only eval path: the legacy
single-``Objective`` ``eval`` (one character shared across a flat
``--seeds`` list) has been retired -- ``cli.py``'s ``eval`` subcommand
resolves a published catalog ``ObjectiveSpec`` (``--objective <name>``) and
calls this function directly.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from nethackers import _image_pins, image_inputs
from nethackers.arena.result_io import read_result_json
from nethackers.arena.seeds import trajectory_spec
from nethackers.containers import (
    container_name,
    container_runtime,
    label_args,
    runtime_capacity,
)
from nethackers.contracts.models import Evidence, Objective, ObjectiveSpec, TrajectoryResult
from nethackers.sandbox_flags import offline_flags
from nethackers.solution_root import require_solution_root

_ARENA_EPISODE = re.compile(
    r"episode (\d+)/(\d+) \((.*?)\): progress=([0-9.]+) (\S+) turns=(\d+) depth=(\d+)"
)

_STDERR_TAIL_LINES = 40  # a docker/arena failure is a handful of lines; keep enough
                         # for context, bounded so a chatty run can't grow memory


def _stream_episodes(
    cmd: list[str], spec: ObjectiveSpec, on_episode: Callable[[dict], None], popen,
    stdin_payload: str,
) -> None:
    """Run the arena container, forwarding each parsed per-episode stderr line
    to ``on_episode`` for live display. Results still come from the mounted
    results.json -- this is display-only. Raises like ``check=True`` on a
    non-zero exit; unparseable lines (warnings, the 'running N' banner) are
    ignored, so the seed comes from ``spec.batch`` order, not the text.

    ``stdin_payload`` is the same pre-derived-specs JSON ``runner(...,
    input=...)`` feeds the non-streaming path (threat 3 a,b -- the secret
    never enters the container on either path) -- only the invocation
    mechanism (Popen vs run) differs, not what the container receives. Fed
    to the child from a separate thread rather than written inline here:
    writing the whole payload on this thread BEFORE draining ``stderr``
    below would deadlock once the payload outgrows the pipe's OS buffer --
    this thread blocks on the child reading stdin, while the child can
    itself be blocked writing a full stderr pipe nobody is draining yet,
    and neither side ever proceeds. This is the exact hazard
    ``subprocess.communicate()`` avoids by never sequencing a blocking write
    then a blocking read on one thread; batches are tiny today so it was
    latent rather than observed, but the shape is cheap to close."""
    proc = popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                 stderr=subprocess.PIPE, text=True, bufsize=1)

    def _feed_stdin() -> None:
        with suppress(BrokenPipeError, OSError):
            proc.stdin.write(stdin_payload)
        with suppress(OSError):
            proc.stdin.close()

    writer = threading.Thread(target=_feed_stdin, daemon=True)
    writer.start()
    tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
    for line in proc.stderr:
        tail.append(line)
        m = _ARENA_EPISODE.search(line)
        if m is None:
            continue
        index = int(m.group(1))
        seed = spec.batch[index - 1][0] if index - 1 < len(spec.batch) else None
        on_episode(
            {
                "index": index,
                "total": int(m.group(2)),
                "seed": seed,
                "character": m.group(3),
                "progress": float(m.group(4)),
                "status": m.group(5),
                "turns": int(m.group(6)),
                "depth": int(m.group(7)),
            }
        )
    returncode = proc.wait()
    writer.join()  # the write is done (or gave up) well before the process exits
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd, stderr="".join(tail))


def _solution_digest(solution_path: Path) -> str:
    """Content hash of every file under ``solution_path``.

    Hashes each file's path (relative to ``solution_path``, POSIX-style) and
    bytes, in sorted-path order, so the digest is stable across machines and
    changes if any file's content or relative location changes.
    """
    h = hashlib.sha256()
    for f in sorted(p for p in solution_path.rglob("*") if p.is_file()):
        h.update(f.relative_to(solution_path).as_posix().encode())
        h.update(f.read_bytes())
    return "sha256:" + h.hexdigest()


def _default_image_digest(image: str, *, runtime: str | None = None) -> str:
    """Resolve ``image`` to a content digest via ``<runtime> image inspect``.

    A ref that is ALREADY digest-pinned (``repo@sha256:...`` -- what
    ``resolve_image`` returns for the arena) is returned VERBATIM, without
    consulting the runtime at all. It already names exactly the bytes that
    ran, and this string now gates hub admission (spec 2026-09-14 D5, via
    ``arena_version.major_for``), so it must not depend on how a runtime
    happens to order its metadata: ``RepoDigests`` is a list, its order is an
    implementation detail, and podman (issue #50) need not put the same entry
    first that docker does. A mirrored or renamed repo entry winning index 0
    would turn a correctly pinned run into an unclassified one at register.

    Otherwise (a tag -- a local dev build, or an ``--image`` override) it
    shells out: prefers the first RepoDigest (present once an image has been
    pushed to/pulled from a registry); falls back to the image Id
    (``sha256:...``) for locally-built images that have no RepoDigests yet.
    Only ever invoked as the default digest resolver -- tests always inject a
    fake resolver instead, so this shells out to a real runtime binary only
    when actually evaluating. ``runtime`` resolves to ``container_runtime()``
    (docker OR podman -- issue #50) when not given, so ``launch.py``'s
    provenance use is podman-aware too without threading a name through.
    """
    if "@sha256:" in image:
        return image
    rt = runtime or container_runtime() or "docker"
    out = subprocess.run(
        [
            rt, "image", "inspect", "--format",
            "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}",
            image,
        ],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# How many of a batch's episodes the arena container runs at once when the
# caller doesn't say AND the container runtime can't be asked what it has.
# Normally the default comes from the machine instead -- see ``_size_box``.
#
# Not purely a speed knob: ``action_timeout_seconds`` is WALL-CLOCK
# (``arena/sandbox.py``'s ``connection.poll``), so oversubscribing a host can
# cut normal actions and depress the score -- see the ACTION_TIMEOUT_SECONDS
# note in ``hub/objectives.py`` for the time this corrupted the hub baseline.
DEFAULT_MAX_PARALLEL_EVALS = 8

# Memory budgeted per concurrent episode. The largest AutoAscend episode (bot
# plus NLE worker) peaked at 640 MB on full-length games (2026-09-21); an
# episode killed for memory is scored 0 without any error, so this errs high.
_EPISODE_MEMORY = 1 << 30
# `--pids-limit` per concurrent episode: ~3 tasks each (42 at 8 wide, 62 at
# 15), so 32 still stops a fork bomb fast. At 8 wide it's the old fixed 256.
_PIDS_PER_EPISODE = 32


def _size_box(batch_size: int, requested: int | None,
              capacity: tuple[int, int] | None) -> tuple[int, int, str | None]:
    """``(episodes, memory_bytes, warning)`` for the sealed box: how many of
    the batch's episodes it runs at once and how much memory it may use.

    One box runs ``episodes`` side by side -- each an NLE worker process plus
    its bot process -- so its caps must come from that count and from the
    machine, never be fixed: 0.34.0's fixed ``--cpus 2`` held 8 episodes to a
    quarter core each (3.7x slower), and its fixed ``--memory 4g`` OOM-killed
    bots at 15. ``capacity`` is ``runtime_capacity``'s ``(cpus,
    memory_bytes)`` -- what the runtime can actually give containers, the
    Docker Desktop VM on a Mac -- or ``None`` when it couldn't be read, which
    falls back to today's fixed default. ``requested`` (an explicit
    ``--max-parallel-evals``) always wins; ``warning`` is set when it asks for
    more memory than the box may use."""
    if capacity is None:
        episodes = requested if requested is not None else DEFAULT_MAX_PARALLEL_EVALS
        episodes = max(1, min(episodes, batch_size))
        return episodes, episodes * _EPISODE_MEMORY, None
    cpus, memory = capacity
    box_memory = memory * 3 // 4   # a quarter stays with the OS, the daemon, the mutator
    if requested is None:
        # Parallelism is bounded by the box's memory, not the machine's, so
        # the default can never trip the warning below.
        episodes = max(1, min(cpus, box_memory // _EPISODE_MEMORY, batch_size))
        return episodes, box_memory, None
    episodes = max(1, min(requested, batch_size))
    warning = None
    if episodes * _EPISODE_MEMORY > box_memory:
        warning = (f"{episodes} episodes at once are budgeted "
                   f"{episodes * _EPISODE_MEMORY / (1 << 30):.0f} GiB, but the arena may use "
                   f"{box_memory / (1 << 30):.1f} of this machine's {memory / (1 << 30):.1f} "
                   f"GiB; an episode that runs out of memory scores 0 -- lower "
                   f"--max-parallel-evals")
    return episodes, box_memory, warning

# An outer wall-clock DoS bound the per-action timeout doesn't give (spec
# sec3b, threat 4): the per-action hang-guard (arena/sandbox.py's
# connection.poll) only fires while the bot is talking to the harness, so a
# submission that never calls back (e.g. forks past --pids-limit and only
# some children respect signals) would otherwise run until the batch's own
# step/no-progress ceilings, or never. `timeout` is in-image coreutils, run
# INSIDE the container so it applies even though the container itself is
# invoked with `check=True`/no host-side timeout.
#
# Per wave, not per batch: each worker runs its share of the batch one
# episode after another, and a timeout loses the whole batch (results.json is
# written only after the last episode), so one fixed bound failed every batch
# long enough -- and a 6-identity cold-start union is already 90 episodes.
WALL_TIMEOUT_S = 3600


def _eval_temp_dir() -> tempfile.TemporaryDirectory:
    """Create a Docker Desktop-shareable temporary arena output directory.

    Docker Desktop does not necessarily share the host's system ``/tmp``.
    The user's home directory is shared by default, so keep disposable arena
    output beneath nethackers' own home-backed data directory instead. Both
    creating the root *and* creating a child can fail in a sandbox/read-only
    home, so the fallback covers the complete allocation rather than only the
    root ``mkdir``. The fallback is the system temp dir, which is what every
    eval used before this preference existed.

    The dir is made world-writable: the arena container writes ``results.json``
    here as uid 65534 (nobody -- the arena never runs untrusted solution code
    as the host user), but ``tempfile`` creates the dir 0700 owned by whoever
    launched the eval, which uid 65534 cannot write on native Linux. (Docker
    Desktop's uid remapping hides this on macOS; a bind mount on native Linux
    keeps the host's ownership.) The dir is ephemeral, per-run, and holds only
    disposable arena output -- never a credential -- so 0777 is the same
    reasoning as the codex cage.
    """
    preferred = Path.home() / ".nethackers" / "tmp"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        td = tempfile.TemporaryDirectory(prefix="arena-", dir=preferred)
    except OSError:
        td = tempfile.TemporaryDirectory(prefix="arena-")
    Path(td.name).chmod(0o777)
    return td


def eval_batch(
    solution_path: str | Path,
    spec: ObjectiveSpec,
    image: str,
    *,
    now: str,
    secret: str = "public",
    runtime: str = "docker",
    runner=subprocess.run,
    image_digest_resolver=None,
    on_episode: Callable[[dict], None] | None = None,
    popen=subprocess.Popen,
    max_parallel_evals: int | None = None,
) -> Evidence:
    """Evaluate ``solution_path`` against ``image`` for ``spec``'s published
    ``(seed, character)`` batch and return the resulting ``Evidence``.

    Runs ``<runtime> run --rm -i --entrypoint timeout`` (``runtime`` is the
    resolved container CLI -- ``"docker"`` or ``"podman"``, issue #50 --
    defaulting to ``"docker"``; ``cli.py``'s handler passes
    ``container_runtime()``) sealed by ``offline_flags()`` (spec sec3b,
    threat 4: no network, read-only rootfs, every capability dropped, no
    privilege escalation, resource caps sized per concurrent episode, a
    non-root user), with the solution
    bind-mounted read-only at ``/sol`` and a fresh host temp directory
    bind-mounted at ``/out``. ``--entrypoint timeout`` OVERRIDES the image's
    baked entrypoint (``python -m nethackers.arena.run``) with bare
    ``timeout`` (coreutils, present in the debian-slim base), and the
    post-image command re-states the full invocation as ``timeout``'s own
    argv -- ``str(WALL_TIMEOUT_S * waves), "python", "-m",
    "nethackers.arena.run", ...`` -- so the wall-clock bound
    (``WALL_TIMEOUT_S`` per wave, an outer DoS bound the per-action timeout
    doesn't give) WRAPS the whole python process,
    rather than being appended as bogus CMD args to it (which argparse
    rejects outright, failing every real call -- a regression only a real
    docker daemon caught). ``timeout`` passes stdin through to the wrapped
    command untouched.

    The (possibly hidden -- worker/verify.py's verified tier) ``secret``
    never enters the container (threat 3 a,b / INV3): this function derives
    each batch entry's concrete ``TrajectorySpec`` HOST-side, via the exact
    same pure ``trajectory_spec(secret, "local", seed)`` the container used
    to call internally -- so the games played are bit-for-bit identical to
    before -- JSON-serializes ``[{"spec": ..., "character": ...}, ...]``, and
    pipes it into the wrapped ``nethackers.arena.run`` over stdin (``-i``
    keeps the pipe open; ``runner(..., input=...)`` on the non-streaming
    path, ``_stream_episodes``'s own threaded write on the streaming one).
    The container's argv/env carry only ``spec``'s step/timeout parameters
    and ``--max-parallel-evals`` -- how many of the batch's episodes the
    container runs concurrently, ``max_parallel_evals`` or, when that is
    ``None``, sized from the machine by ``_size_box`` -- never a seed or the
    secret that derived it.
    Reads back ``/out/results.json`` defensively via ``read_result_json``
    (spec sec3b, INV4 -- the box's output is HOSTILE data: rejects a symlink
    escape, an oversized file, or malformed/non-list JSON) into a
    ``list[TrajectoryResult.to_dict()]``, one per batch entry in batch order
    regardless of completion order, and wraps it into an ``Evidence``.

    ``--platform linux/amd64`` is added only when ``image`` is the arena pin
    (``_image_pins.ARENA_IMAGE``), where it is cosmetic. On any other ref it
    would be fatal rather than cosmetic -- see the comment at the call site.

    ``evaluator_image`` is set to ``image_digest_resolver(image)`` -- the
    image's resolved content digest, not the (mutable) ``image`` tag passed
    in -- so evidence records exactly which image bytes produced it.
    ``image_digest_resolver`` defaults (when ``None``) to ``_default_image_
    digest`` bound to ``runtime`` (a thin ``<runtime> image inspect`` shell)
    but, like ``runner``, is injectable so tests never need a real daemon.

    ``Evidence.objective`` is typed ``Objective`` (a single-build config),
    but a batch spans multiple characters, so this synthesizes a faithful
    multi-character descriptor instead of picking one build: ``character``
    is ``None`` (no single build represents the batch) and ``seed_set`` is
    ``spec.name``. This descriptor is *not* the authoritative record of
    per-episode identity -- that lives in ``results[*].character`` -- nor is
    it how the objective is later re-derived (register/store tooling does
    that from the published catalog by ``spec.name``).
    """
    # Refuse a root that cannot be scored BEFORE the -v mount, because the
    # mount is what hid the failure: docker CREATES an absent bind-mount
    # source, so a missing or typo'd path used to run the whole batch against
    # an empty directory and report a clean mean_progress of 0.0 at exit code
    # 0. Every scoring path funnels through here -- cli eval/submit, the evolve
    # loop, the public baseline, the hidden-seed verifier -- so this one call
    # is what makes that number unreachable. It also resolves AutoAscend's
    # canonical names to the packaged tree, so `roots/autoascend` works for a
    # pip install that has no checkout to resolve it against.
    solution_path = require_solution_root(solution_path)
    # Absolutize before the -v mount: docker rejects a relative bind-mount
    # source (it reads it as an invalid named volume). Callers in the evolve
    # loop pass absolute worktree paths, but the AutoAscend baseline passes a
    # repo-relative tree. .absolute() only prefixes the cwd -- it never resolves
    # symlinks, so the content digest below (relative-path based) is unchanged.
    solution_path = solution_path.absolute()
    # Bind the default digest resolver to the SAME resolved runtime the run
    # uses (docker/podman -- issue #50); an injected resolver (tests) wins.
    resolve_digest = image_digest_resolver or (
        lambda img: _default_image_digest(img, runtime=runtime)
    )
    with _eval_temp_dir() as td:
        # --platform is cosmetic FOR THE PIN and only for it: ARENA_IMAGE is an
        # amd64 MANIFEST digest, so the architecture is already decided (spec
        # 2026-09-14 D2) and the flag merely suppresses the mismatch warning
        # Docker prints on every emulated run.
        #
        # It is NOT harmless on any other ref. `docker run --platform
        # linux/amd64` against a locally built arm64-only image FAILS ("pull
        # access denied" -- the daemon finds no amd64 variant and falls through
        # to a registry pull), which would break both paths D6 deliberately
        # keeps open: `--image`/NETHACKERS_ARENA_IMAGE for arena development,
        # and the per-worktree `arena:<slug>` of docs/local-stack.md. So it is
        # passed only when the resolved ref IS the pin.
        platform = (["--platform", image_inputs.REFERENCE_PLATFORM]
                    if image == _image_pins.ARENA_IMAGE else [])
        # Derive the concrete per-trajectory seeds HOST-side (threat 3 a,b /
        # INV3): `trajectory_spec` is the exact pure function the container
        # used to call internally with these same (secret, "local", seed)
        # inputs, so the derived specs -- and therefore the games played --
        # are unchanged; only WHERE the secret is consumed moves. The secret
        # itself stops here -- only the derived specs are serialized.
        specs_payload = json.dumps(
            [
                {
                    "spec": trajectory_spec(secret, "local", int(seed)).to_dict(),
                    "character": character,
                }
                for seed, character in spec.batch
            ]
        )
        # The box runs `episodes` at once and each worker takes `waves` turns
        # through the batch; the resource caps and the wall-clock bound scale
        # with them (see _size_box).
        episodes, box_memory, warning = _size_box(
            len(spec.batch), max_parallel_evals, runtime_capacity(runtime))
        if warning:
            print(f"arena · warning: {warning}", file=sys.stderr, flush=True)
        waves = max(1, math.ceil(len(spec.batch) / episodes))
        cmd = [
            runtime, "run", *platform, "--rm", "-i",  # -i: keep stdin open for the specs
            "--name", container_name("arena"), *label_args(),
            # Sealed box (spec sec3b, threat 4): no network, read-only rootfs
            # (with a noexec/nosuid tmpfs for scratch space), every Linux
            # capability dropped, no privilege escalation, and resource caps
            # -- a malicious or merely buggy submission (fork bomb, OOM, a
            # reverse shell) dies inside the container instead of touching
            # the host or the network. The caps are per concurrent episode.
            *offline_flags(
                cpus=episodes,   # one core each: worker and bot take turns
                memory=f"{box_memory >> 20}m",
                pids=_PIDS_PER_EPISODE * episodes,
            ),
            # Silence AutoAscend's numpy RuntimeWarning flood at interpreter
            # startup, for every process in the container (a plain in-arena
            # filter didn't hold -- NLE/AutoAscend resets it). No secret on
            # this env block (or anywhere else in argv) -- see the docstring.
            "-e", "PYTHONWARNINGS=ignore::RuntimeWarning",
            # offline_flags()'s non-root --user has no writable $HOME under
            # the read-only rootfs; NLE/numba want one to cache into, and the
            # tmpfs mounted at /tmp is the only writable, non-bind-mounted
            # path offline_flags() leaves available.
            "-e", "HOME=/tmp",
            "-v", f"{solution_path}:/sol:ro",
            "-v", f"{td}:/out",
            # Override the image's baked ENTRYPOINT (`python -m nethackers.
            # arena.run`) with bare `timeout` so the wall-clock bound below
            # WRAPS the whole python invocation, instead of being appended
            # as CMD args to the original entrypoint -- which argparse
            # rejects outright ("unrecognized arguments: timeout 3600"),
            # failing EVERY real eval_batch call (self-report eval/submit,
            # evolve scoring, the verified-tier worker). `timeout` (coreutils,
            # present in the debian-slim base) passes stdin through
            # untouched, so the specs piped above still reach python.
            "--entrypoint", "timeout",
            image,
            # An outer wall-clock DoS bound -- see WALL_TIMEOUT_S. Re-states
            # the full invocation as `timeout`'s own argv (`timeout N cmd
            # [args...]`) since overriding the entrypoint above means this
            # image no longer runs `python -m nethackers.arena.run` on its
            # own -- something has to say so explicitly now.
            str(WALL_TIMEOUT_S * waves), "python", "-m", "nethackers.arena.run",
            "--solution", "/sol",
            "--max-steps", str(spec.max_steps),
            "--no-progress-timeout", str(spec.no_progress_timeout),
            "--action-timeout", str(spec.action_timeout_seconds),
            "--max-parallel-evals", str(episodes),
            "--out", "/out/results.json",
        ]
        if on_episode is None:
            # subprocess.run's `input=` needs either text mode or bytes; the
            # payload is only ever a plain JSON string, so encode it rather
            # than thread `text=True` through the (test-facing) runner seam.
            runner(cmd, check=True, input=specs_payload.encode())
        else:
            _stream_episodes(cmd, spec, on_episode, popen, specs_payload)
        # Defensive read (spec sec3b, INV4): the box's output is HOSTILE
        # data -- reject a symlink escape, an oversized file, or
        # malformed/non-list JSON before trusting anything it wrote.
        results = [TrajectoryResult.from_dict(r) for r in read_result_json(Path(td))]
    objective = Objective(
        character=None,
        max_steps=spec.max_steps,
        no_progress_timeout=spec.no_progress_timeout,
        action_timeout_seconds=spec.action_timeout_seconds,
        seed_set=spec.name,
    )
    return Evidence.from_results(
        solution_digest=_solution_digest(solution_path),
        objective=objective,
        evaluator_image=resolve_digest(image),
        results=results,
        created_at=now,
    )
