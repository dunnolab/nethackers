"""Local ``eval`` orchestrator: runs a solution through the pinned arena
Docker image for a set of trajectory ids and wraps the resulting
``list[TrajectoryResult]`` JSON into an ``Evidence`` record.

This module never talks to Docker directly -- ``eval`` shells out via an
injectable ``runner`` callable (default ``subprocess.run``), so
tests/test_eval_runner.py exercises the command-building and
result-wrapping logic with a fake runner and never needs a real Docker
daemon or the NLE-backed arena image (that full-stack path is
tests/test_docker_smoke.py, gated behind the ``docker`` marker).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult


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
