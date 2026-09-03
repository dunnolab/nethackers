"""Score a solution tree on an ObjectiveSpec via the arena (eval_batch)."""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.eval.runner import eval_batch


def evaluate(
    tree: str | Path,
    spec: ObjectiveSpec,
    image: str,
    *,
    now: str,
    runtime: str = "docker",
    runner=subprocess.run,
    on_episode: Callable[[dict], None] | None = None,
    max_parallel_evals: int = 8,
) -> tuple[float, Evidence]:
    # ``runtime`` is the resolved container CLI (docker/podman -- issue #50),
    # threaded from ``run_loop`` so evolve's arena evals use the same binary the
    # rest of the run does; defaults to ``"docker"`` for back-compat.
    evidence = eval_batch(
        tree, spec, image, now=now, runtime=runtime, runner=runner,
        image_digest_resolver=lambda img: img, on_episode=on_episode,
        max_parallel_evals=max_parallel_evals,
    )
    return evidence.mean_progress, evidence
