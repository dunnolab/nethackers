"""In-image CLI entrypoint for the pinned arena Docker image.

Drives an evaluation via ``--batch``: JSON ``[[seed, character], ...]`` --
runs one trajectory per ``(seed, character)`` pair, fanned out concurrently
across worker processes via :func:`run_batch` (capped by
``--max-parallel-evals``, default 8), feeding each pair's ``character`` to
both the environment (NLE build selection) and the recorded result identity
(``character == "-"`` means NLE's natural random draw for that trajectory --
still recorded as the literal ``"-"`` identity, not a resolved build).
Writes the resulting ``list[TrajectoryResult.to_dict()]`` as JSON to
``--out``, in batch order regardless of completion order.

An evaluation id/secret pins the deterministic per-trajectory seeds. This
module is the image's ``ENTRYPOINT`` (``python -m nethackers.arena.run``);
it is the only supported way to drive an evaluation inside the pinned,
deterministic container described by ``arena/Dockerfile``. The legacy
single-character ``--character``/``--seeds`` path (M1) has been retired --
``--batch`` is the only supported way in.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import warnings
from collections.abc import Callable
from pathlib import Path

from nethackers.arena.seeds import trajectory_spec
from nethackers.arena.trajectory import run_trajectory
from nethackers.contracts.models import (
    DEFAULT_MAX_STEPS,
    DEFAULT_NO_PROGRESS_TIMEOUT,
    Objective,
    TrajectoryResult,
)


def _failed_result(trajectory_id: int, character: str, error: object) -> TrajectoryResult:
    """A dead worker process (segfault/OOM) never returns a result; synthesize a
    zero-progress infrastructure_error so the batch still yields N results."""
    return TrajectoryResult(
        trajectory_id=trajectory_id, status="infrastructure_error", progress=0.0,
        ascended=False, steps=0, turns=0, max_depth=1, end_status=None,
        error=str(error)[-8_000:], wall_seconds=0.0, character=character, milestone=None)


def run_batch(
    submission_path: str,
    batch: list,
    *,
    secret: str,
    evaluation_id: str,
    max_steps: int,
    no_progress_timeout: int,
    action_timeout: float,
    max_parallel_evals: int,
    on_episode: Callable[[int, TrajectoryResult], None] | None = None,
    run_one: Callable[..., TrajectoryResult] = run_trajectory,
    executor_factory=concurrent.futures.ProcessPoolExecutor,
) -> list[TrajectoryResult]:
    """Run every (seed, character) in ``batch`` concurrently across worker
    processes, capped at ``min(max_parallel_evals, len(batch))``. Results come
    back in batch order; ``on_episode(index, result)`` fires as each finishes
    (out of order). ``run_one``/``executor_factory`` are injected for tests."""
    n = len(batch)
    results: list[TrajectoryResult | None] = [None] * n
    if n == 0:
        return []
    workers = max(1, min(max_parallel_evals, n))
    prepared = []
    for i, (seed, char) in enumerate(batch):
        spec = trajectory_spec(secret, evaluation_id, int(seed))
        objective = Objective(
            None if char == "-" else char, max_steps, no_progress_timeout,
            action_timeout, "runtime")
        prepared.append((i, char, spec, objective))
    with executor_factory(max_workers=workers) as ex:
        fut_to_job = {
            ex.submit(run_one, submission_path, spec, objective, char): (i, char, spec)
            for (i, char, spec, objective) in prepared
        }
        for fut in concurrent.futures.as_completed(fut_to_job):
            i, char, spec = fut_to_job[fut]
            try:
                result = fut.result()
            except Exception as error:  # worker process died hard
                result = _failed_result(spec.trajectory_id, char, error)
            results[i] = result
            if on_episode is not None:
                on_episode(i, result)
    return results  # type: ignore[return-value]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--solution", required=True)
    p.add_argument("--batch", required=True)  # JSON [[seed, character], ...]
    p.add_argument(
        "--evaluation-id", default="local",
        help="Seed namespace (NLE seeds = HMAC(secret, evaluation-id, trajectory)). "
             "Default 'local' is exactly what the evolve judge scores on -- omit it to "
             "match the judge; a different value evaluates on different, unrelated games.")
    p.add_argument("--secret", default="public")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--no-progress-timeout", type=int, default=DEFAULT_NO_PROGRESS_TIMEOUT)
    # 120s LOCAL hang-guard default (see hub/objectives.py ACTION_TIMEOUT_SECONDS):
    # only a genuinely hung bot times out; a normal (or cold-JIT) action is never
    # cut. The validator passes its own --action-timeout; it must not inherit this.
    p.add_argument("--action-timeout", type=float, default=120.0)
    p.add_argument("--max-parallel-evals", type=int, default=8)
    p.add_argument("--out", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    # AutoAscend floods stderr with numpy RuntimeWarnings (e.g. tty_cursor
    # underflow at agent.py:371). Silence them by default so the per-episode
    # progress is readable; re-enable with NETHACKERS_ARENA_WARNINGS=1.
    if os.environ.get("NETHACKERS_ARENA_WARNINGS") != "1":
        warnings.filterwarnings("ignore", category=RuntimeWarning)
    a = _parser().parse_args(argv)
    secret = os.environ.get("NETHACK_ARENA_SECRET") or a.secret
    sys.path.insert(0, a.solution)  # so `import bot`, `import arena_adapter` resolve

    # Published (seed, character) batch -- one trajectory per pair, fanned
    # out concurrently by run_batch (capped at --max-parallel-evals). The
    # pair's character drives both the environment (via objective.character)
    # and the recorded result identity (via character=), so the recorded
    # identity matches the played build. run_batch returns results in batch
    # order regardless of completion order.
    batch = json.loads(a.batch)
    total = len(batch)
    # Per-episode progress on stderr (flushed) -- `eval_batch` runs this
    # container with inherited stderr, so these lines stream live to the
    # harness's terminal, turning a multi-minute silent eval into visible
    # "episode k/N" progress. Lines arrive in completion order, not batch
    # order, once episodes run concurrently.
    print(f"arena · running {total} episode(s)…", file=sys.stderr, flush=True)

    def _on_episode(i: int, result) -> None:
        _seed, char = batch[i]
        print(
            f"arena · episode {i + 1}/{total} ({char}): progress={result.progress:.3f}"
            f" {result.status} turns={result.turns} depth={result.max_depth}",
            file=sys.stderr,
            flush=True,
        )

    results = run_batch(
        a.solution, batch, secret=secret, evaluation_id=a.evaluation_id,
        max_steps=a.max_steps, no_progress_timeout=a.no_progress_timeout,
        action_timeout=a.action_timeout, max_parallel_evals=a.max_parallel_evals,
        on_episode=_on_episode,
    )
    Path(a.out).write_text(json.dumps([r.to_dict() for r in results]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
