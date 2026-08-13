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
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from nethackers.contracts.models import Evidence, Objective, ObjectiveSpec, TrajectoryResult

_ARENA_EPISODE = re.compile(
    r"episode (\d+)/(\d+) \((.*?)\): progress=([0-9.]+) (\S+) turns=(\d+) depth=(\d+)"
)


def _stream_episodes(
    cmd: list[str], spec: ObjectiveSpec, on_episode: Callable[[dict], None], popen
) -> None:
    """Run the arena container, forwarding each parsed per-episode stderr line
    to ``on_episode`` for live display. Results still come from the mounted
    results.json -- this is display-only. Raises like ``check=True`` on a
    non-zero exit; unparseable lines (warnings, the 'running N' banner) are
    ignored, so the seed comes from ``spec.batch`` order, not the text."""
    proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    for line in proc.stderr:
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
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)


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


def _default_image_digest(image: str) -> str:
    """Resolve ``image`` to a content digest via ``docker image inspect``.

    Prefers the first RepoDigest (``repo@sha256:...``, present once an image
    has been pushed to/pulled from a registry); falls back to the image Id
    (``sha256:...``) for locally-built images that have no RepoDigests yet.
    Only ever invoked as the default ``image_digest_resolver`` -- tests
    always inject a fake resolver instead, so this shells out to a real
    ``docker`` binary only when actually evaluating.
    """
    out = subprocess.run(
        [
            "docker", "image", "inspect", "--format",
            "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}",
            image,
        ],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def eval_batch(
    solution_path: str | Path,
    spec: ObjectiveSpec,
    image: str,
    *,
    now: str,
    runner=subprocess.run,
    image_digest_resolver=_default_image_digest,
    on_episode: Callable[[dict], None] | None = None,
    popen=subprocess.Popen,
    max_parallel_evals: int = 8,
) -> Evidence:
    """Evaluate ``solution_path`` against ``image`` for ``spec``'s published
    ``(seed, character)`` batch and return the resulting ``Evidence``.

    Runs ``docker run --rm --network none`` with the solution bind-mounted
    read-only at ``/sol`` and a fresh host temp directory bind-mounted at
    ``/out``, invoking the image's ``nethackers.arena.run`` entrypoint with
    ``--batch`` (JSON ``[[seed, character], ...]``, replacing the legacy
    ``--character``/``--seeds``), ``spec``'s step/timeout parameters, and
    ``--max-parallel-evals`` (``max_parallel_evals``, default 8) -- the cap
    on how many of the batch's episodes the container runs concurrently.
    Reads back ``/out/results.json`` (a ``list[TrajectoryResult.to_dict()]``,
    one per batch entry in batch order regardless of completion order) and
    wraps it into an ``Evidence``.

    ``evaluator_image`` is set to ``image_digest_resolver(image)`` -- the
    image's resolved content digest, not the (mutable) ``image`` tag passed
    in -- so evidence records exactly which image bytes produced it.
    ``image_digest_resolver`` defaults to ``_default_image_digest`` (a thin
    ``docker image inspect`` shell) but, like ``runner``, is injectable so
    tests never need a real Docker daemon.

    ``Evidence.objective`` is typed ``Objective`` (a single-build config),
    but a batch spans multiple characters, so this synthesizes a faithful
    multi-character descriptor instead of picking one build: ``character``
    is ``None`` (no single build represents the batch) and ``seed_set`` is
    ``spec.name``. This descriptor is *not* the authoritative record of
    per-episode identity -- that lives in ``results[*].character`` -- nor is
    it how the objective is later re-derived (register/store tooling does
    that from the published catalog via ``spec.digest()``).
    """
    solution_path = Path(solution_path)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "results.json"
        cmd = [
            "docker", "run", "--rm", "--network", "none",
            # Silence AutoAscend's numpy RuntimeWarning flood at interpreter
            # startup, for every process in the container (a plain in-arena
            # filter didn't hold -- NLE/AutoAscend resets it).
            "-e", "PYTHONWARNINGS=ignore::RuntimeWarning",
            "-v", f"{solution_path}:/sol:ro",
            "-v", f"{td}:/out",
            image,
            "--solution", "/sol",
            "--batch", json.dumps([[seed, character] for seed, character in spec.batch]),
            "--evaluation-id", "local",
            "--max-steps", str(spec.max_steps),
            "--no-progress-timeout", str(spec.no_progress_timeout),
            "--action-timeout", str(spec.action_timeout_seconds),
            "--max-parallel-evals", str(max_parallel_evals),
            "--out", "/out/results.json",
        ]
        if on_episode is None:
            runner(cmd, check=True)
        else:
            _stream_episodes(cmd, spec, on_episode, popen)
        results = [TrajectoryResult.from_dict(r) for r in json.loads(out.read_text())]
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
        evaluator_image=image_digest_resolver(image),
        results=results,
        created_at=now,
    )
