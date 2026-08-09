"""Local ``eval`` orchestrator: runs a solution through the pinned arena
Docker image for a set of trajectory ids and wraps the resulting
``list[TrajectoryResult]`` JSON into an ``Evidence`` record.

This module never talks to Docker directly -- ``eval`` shells out via an
injectable ``runner`` callable (default ``subprocess.run``), so
tests/test_eval_runner.py exercises the command-building and
result-wrapping logic with a fake runner and never needs a real Docker
daemon or the NLE-backed arena image (that full-stack path is
tests/test_docker_smoke.py, gated behind the ``docker`` marker).

M2a additionally provides ``eval_batch``: runs a published ``ObjectiveSpec``
batch -- a fixed, ordered ``((seed, character), ...)`` list -- as one episode
per pair, and records ``evaluator_image`` as the evaluator image's resolved
content *digest* (via the injectable ``image_digest_resolver``, default
``_default_image_digest``) rather than its mutable tag, so evidence stays
attributable to the exact image bytes that produced it. ``eval_batch`` is
additive alongside the legacy ``eval`` (kept as-is -- see Task 13 for its
eventual removal once ``cli.py`` migrates to the batch path).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from nethackers.contracts.models import Evidence, Objective, ObjectiveSpec, TrajectoryResult


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


def eval(
    solution_path: str | Path,
    objective: Objective,
    image: str,
    *,
    seed_ids: list[int],
    now: str,
    runner=subprocess.run,
) -> Evidence:
    """Evaluate ``solution_path`` against ``image`` for ``seed_ids`` and
    return the resulting ``Evidence``.

    Runs ``docker run --rm --network none`` with the solution bind-mounted
    read-only at ``/sol`` and a fresh host temp directory bind-mounted at
    ``/out``, invoking the image's ``nethackers.arena.run`` entrypoint
    (``arena/Dockerfile``'s ``ENTRYPOINT``) with ``--evaluation-id local``
    and ``objective``'s parameters. Reads back ``/out/results.json`` (a
    ``list[TrajectoryResult.to_dict()]``, per ``arena/run.py``) and wraps it
    into an ``Evidence`` via ``Evidence.from_results`` (tier defaults to
    ``"self-reported"``), with a content-hash ``solution_digest`` over the
    solution directory.

    ``runner`` defaults to ``subprocess.run`` but is injectable so tests
    supply a fake that never launches a real container.
    """
    solution_path = Path(solution_path)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "results.json"
        cmd = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{solution_path}:/sol:ro",
            "-v", f"{td}:/out",
            image,
            "--solution", "/sol",
            "--character", objective.character or "-",
            "--seeds", ",".join(str(i) for i in seed_ids),
            "--evaluation-id", "local",
            "--max-steps", str(objective.max_steps),
            "--no-progress-timeout", str(objective.no_progress_timeout),
            "--action-timeout", str(objective.action_timeout_seconds),
            "--out", "/out/results.json",
        ]
        runner(cmd, check=True)
        results = [TrajectoryResult.from_dict(r) for r in json.loads(out.read_text())]
    return Evidence.from_results(
        solution_digest=_solution_digest(solution_path),
        objective=objective,
        evaluator_image=image,
        results=results,
        created_at=now,
    )


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
) -> Evidence:
    """Evaluate ``solution_path`` against ``image`` for ``spec``'s published
    ``(seed, character)`` batch and return the resulting ``Evidence``.

    Runs ``docker run --rm --network none`` with the solution bind-mounted
    read-only at ``/sol`` and a fresh host temp directory bind-mounted at
    ``/out``, invoking the image's ``nethackers.arena.run`` entrypoint with
    ``--batch`` (JSON ``[[seed, character], ...]``, replacing the legacy
    ``--character``/``--seeds``) and ``spec``'s step/timeout parameters.
    Reads back ``/out/results.json`` (a ``list[TrajectoryResult.to_dict()]``,
    one per batch entry in batch order) and wraps it into an ``Evidence``.

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
            "-v", f"{solution_path}:/sol:ro",
            "-v", f"{td}:/out",
            image,
            "--solution", "/sol",
            "--batch", json.dumps([[seed, character] for seed, character in spec.batch]),
            "--evaluation-id", "local",
            "--max-steps", str(spec.max_steps),
            "--no-progress-timeout", str(spec.no_progress_timeout),
            "--action-timeout", str(spec.action_timeout_seconds),
            "--out", "/out/results.json",
        ]
        runner(cmd, check=True)
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
