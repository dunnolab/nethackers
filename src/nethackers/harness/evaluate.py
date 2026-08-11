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
    runner=subprocess.run,
    on_episode: Callable[[dict], None] | None = None,
) -> tuple[float, Evidence]:
    evidence = eval_batch(
        tree, spec, image, now=now, runner=runner,
        image_digest_resolver=lambda img: img, on_episode=on_episode,
    )
    return evidence.mean_progress, evidence
