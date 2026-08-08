"""In-image CLI entrypoint for the pinned arena Docker image.

Given a solution directory, a character build (or ``-`` for NLE's natural
random draw), a comma-separated list of trajectory ids, and an evaluation
id/secret, runs one trajectory per id via
:func:`nethackers.arena.trajectory.run_trajectory` and writes the resulting
``list[TrajectoryResult.to_dict()]`` as JSON to ``--out``. This module is the
image's ``ENTRYPOINT`` (``python -m nethackers.arena.run``); it is the only
supported way to drive an evaluation inside the pinned, deterministic
container described by ``arena/Dockerfile``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nethackers.arena.environment import make_environment  # noqa: F401 (import ensures NLE present)
from nethackers.arena.seeds import trajectory_spec
from nethackers.arena.trajectory import run_trajectory
from nethackers.contracts.models import Objective


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--solution", required=True)
    p.add_argument("--character", default="-")  # "-" => random draw
    p.add_argument("--seeds", required=True)  # comma-separated trajectory ids
    p.add_argument("--evaluation-id", required=True)
    p.add_argument("--secret", default="public")
    p.add_argument("--max-steps", type=int, default=1_000_000)
    p.add_argument("--no-progress-timeout", type=int, default=10_000)
    p.add_argument("--action-timeout", type=float, default=5.0)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    sys.path.insert(0, a.solution)  # so `import bot`, `import arena_adapter` resolve
    character = None if a.character == "-" else a.character
    objective = Objective(
        character, a.max_steps, a.no_progress_timeout, a.action_timeout, "runtime"
    )
    ids = [int(x) for x in a.seeds.split(",") if x != ""]
    results = []
    for tid in ids:
        spec = trajectory_spec(a.secret, a.evaluation_id, tid)
        results.append(
            run_trajectory(submission_path=a.solution, spec=spec, objective=objective).to_dict()
        )
    Path(a.out).write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
