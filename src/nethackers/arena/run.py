"""In-image CLI entrypoint for the pinned arena Docker image.

Two ways to drive an evaluation, both writing the resulting
``list[TrajectoryResult.to_dict()]`` as JSON to ``--out``:

- ``--batch`` (M2a): JSON ``[[seed, character], ...]`` -- runs one trajectory
  per ``(seed, character)`` pair via
  :func:`nethackers.arena.trajectory.run_trajectory`, in list order, feeding
  each pair's ``character`` to both the environment (NLE build selection)
  and the recorded result identity. Takes precedence if both ``--batch`` and
  ``--seeds`` are given.
- ``--character``/``--seeds`` (legacy, M1): a single character build (or
  ``-`` for NLE's natural random draw) shared across a comma-separated list
  of trajectory ids. Superseded by ``--batch``; kept for existing callers
  until Task 13 migrates them and removes it.

Either way, an evaluation id/secret pins the deterministic per-trajectory
seeds. This module is the image's ``ENTRYPOINT``
(``python -m nethackers.arena.run``); it is the only supported way to drive
an evaluation inside the pinned, deterministic container described by
``arena/Dockerfile``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nethackers.arena.seeds import trajectory_spec
from nethackers.arena.trajectory import run_trajectory
from nethackers.contracts.models import DEFAULT_MAX_STEPS, DEFAULT_NO_PROGRESS_TIMEOUT, Objective


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--solution", required=True)
    p.add_argument("--character", default="-")  # "-" => random draw (legacy path only)
    p.add_argument("--batch")  # JSON [[seed, character], ...]; precedence over --character/--seeds
    p.add_argument("--seeds")  # legacy; comma-separated trajectory ids
    p.add_argument("--evaluation-id", required=True)
    p.add_argument("--secret", default="public")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--no-progress-timeout", type=int, default=DEFAULT_NO_PROGRESS_TIMEOUT)
    p.add_argument("--action-timeout", type=float, default=5.0)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.batch is None and a.seeds is None:
        p.error("one of --batch or --seeds is required")
    sys.path.insert(0, a.solution)  # so `import bot`, `import arena_adapter` resolve

    results = []
    if a.batch is not None:
        # M2a: published (seed, character) batch -- one trajectory per pair,
        # in batch order. The pair's character drives both the environment
        # (via objective.character) and the recorded result identity (via
        # character=), so the recorded identity matches the played build.
        for seed, char in json.loads(a.batch):
            spec = trajectory_spec(a.secret, a.evaluation_id, int(seed))
            objective = Objective(
                None if char == "-" else char,
                a.max_steps,
                a.no_progress_timeout,
                a.action_timeout,
                "runtime",
            )
            results.append(
                run_trajectory(
                    submission_path=a.solution, spec=spec, objective=objective, character=char
                ).to_dict()
            )
    else:
        # Legacy (M1): a single character shared across --seeds. Unchanged
        # behavior -- slated for removal once Task 13 migrates cli.py.
        character = None if a.character == "-" else a.character
        objective = Objective(
            character, a.max_steps, a.no_progress_timeout, a.action_timeout, "runtime"
        )
        ids = [int(x) for x in a.seeds.split(",") if x != ""]
        for tid in ids:
            spec = trajectory_spec(a.secret, a.evaluation_id, tid)
            results.append(
                run_trajectory(
                    submission_path=a.solution, spec=spec, objective=objective
                ).to_dict()
            )
    Path(a.out).write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
