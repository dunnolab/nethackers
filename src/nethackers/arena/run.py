"""In-image CLI entrypoint for the pinned arena Docker image.

Drives an evaluation via ``--batch``: JSON ``[[seed, character], ...]`` --
runs one trajectory per ``(seed, character)`` pair via
:func:`nethackers.arena.trajectory.run_trajectory`, in list order, feeding
each pair's ``character`` to both the environment (NLE build selection) and
the recorded result identity (``character == "-"`` means NLE's natural
random draw for that trajectory -- still recorded as the literal ``"-"``
identity, not a resolved build). Writes the resulting
``list[TrajectoryResult.to_dict()]`` as JSON to ``--out``.

An evaluation id/secret pins the deterministic per-trajectory seeds. This
module is the image's ``ENTRYPOINT`` (``python -m nethackers.arena.run``);
it is the only supported way to drive an evaluation inside the pinned,
deterministic container described by ``arena/Dockerfile``. The legacy
single-character ``--character``/``--seeds`` path (M1) has been retired --
``--batch`` is the only supported way in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

from nethackers.arena.seeds import trajectory_spec
from nethackers.arena.trajectory import run_trajectory
from nethackers.contracts.models import DEFAULT_MAX_STEPS, DEFAULT_NO_PROGRESS_TIMEOUT, Objective


def main(argv: list[str] | None = None) -> int:
    # AutoAscend floods stderr with numpy RuntimeWarnings (e.g. tty_cursor
    # underflow at agent.py:371). Silence them by default so the per-episode
    # progress is readable; re-enable with NETHACKERS_ARENA_WARNINGS=1.
    if os.environ.get("NETHACKERS_ARENA_WARNINGS") != "1":
        warnings.filterwarnings("ignore", category=RuntimeWarning)
    p = argparse.ArgumentParser()
    p.add_argument("--solution", required=True)
    p.add_argument("--batch", required=True)  # JSON [[seed, character], ...]
    p.add_argument("--evaluation-id", required=True)
    p.add_argument("--secret", default="public")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--no-progress-timeout", type=int, default=DEFAULT_NO_PROGRESS_TIMEOUT)
    p.add_argument("--action-timeout", type=float, default=5.0)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    sys.path.insert(0, a.solution)  # so `import bot`, `import arena_adapter` resolve

    # Published (seed, character) batch -- one trajectory per pair, in batch
    # order. The pair's character drives both the environment (via
    # objective.character) and the recorded result identity (via
    # character=), so the recorded identity matches the played build.
    batch = json.loads(a.batch)
    total = len(batch)
    # Per-episode progress on stderr (flushed) -- `eval_batch` runs this
    # container with inherited stderr, so these lines stream live to the
    # harness's terminal, turning a multi-minute silent eval into visible
    # "episode k/N" progress.
    print(f"arena · running {total} episode(s)…", file=sys.stderr, flush=True)
    results = []
    for i, (seed, char) in enumerate(batch, start=1):
        spec = trajectory_spec(a.secret, a.evaluation_id, int(seed))
        objective = Objective(
            None if char == "-" else char,
            a.max_steps,
            a.no_progress_timeout,
            a.action_timeout,
            "runtime",
        )
        result = run_trajectory(
            submission_path=a.solution, spec=spec, objective=objective, character=char
        )
        print(
            f"arena · episode {i}/{total} ({char}): progress={result.progress:.3f}"
            f" {result.status} turns={result.turns} depth={result.max_depth}",
            file=sys.stderr,
            flush=True,
        )
        results.append(result.to_dict())
    Path(a.out).write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
