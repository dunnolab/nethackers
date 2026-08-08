from __future__ import annotations

import time
import traceback
from contextlib import suppress
from pathlib import Path

from nethackers.arena.environment import EnvironmentMetrics, make_environment
from nethackers.arena.sandbox import AgentClient, BotError, BotTimeout, InvalidAction
from nethackers.contracts.models import Objective, ResultStatus, TrajectoryResult, TrajectorySpec

_EMPTY_METRICS = EnvironmentMetrics(0.0, 0, 1, False, None)


def _result(
    trajectory_id: int,
    status: ResultStatus,
    metrics: EnvironmentMetrics,
    steps: int,
    started: float,
    error: str | None = None,
) -> TrajectoryResult:
    return TrajectoryResult(
        trajectory_id=trajectory_id,
        status=status,
        progress=metrics.progress,
        ascended=metrics.ascended,
        steps=steps,
        turns=metrics.turns,
        max_depth=metrics.max_depth,
        end_status=metrics.end_status,
        error=error[-8_000:] if error else None,
        wall_seconds=round(time.monotonic() - started, 6),
    )


def run_trajectory(
    submission_path: str | Path,
    spec: TrajectorySpec,
    objective: Objective,
    action_count: int,
) -> TrajectoryResult:
    """Run one trajectory: a fresh sandboxed bot against a fresh environment.

    Builds the environment from ``objective`` and the sandbox client from
    ``spec``/``objective``/``action_count``, drives the reset/act/step loop
    until the episode ends, and always tears both down in a ``finally``. Any
    exception is mapped to a terminal ``ResultStatus`` instead of propagating,
    so a caller (e.g. a batch runner) can loop over many trajectories without
    a single bad episode aborting the run.
    """
    started = time.monotonic()
    environment = None
    client = None
    steps = 0
    try:
        environment = make_environment(
            objective.max_steps, objective.no_progress_timeout, objective.character
        )
        client = AgentClient(
            Path(submission_path),
            bot_seed=spec.bot_seed,
            action_count=action_count,
            timeout_seconds=objective.action_timeout_seconds,
        )
        observation = environment.reset(spec)
        client.reset(observation)
        while steps < objective.max_steps:
            action = client.act(observation)
            observation, _reward, terminated, truncated = environment.step(action)
            steps += 1
            if terminated or truncated:
                break
        return _result(spec.trajectory_id, "completed", environment.metrics(), steps, started)
    except InvalidAction as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(spec.trajectory_id, "invalid_action", metrics, steps, started, str(error))
    except BotTimeout as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(spec.trajectory_id, "bot_timeout", metrics, steps, started, str(error))
    except BotError as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(spec.trajectory_id, "bot_error", metrics, steps, started, str(error))
    except Exception:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(
            spec.trajectory_id,
            "infrastructure_error",
            metrics,
            steps,
            started,
            traceback.format_exc(limit=30),
        )
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
        if environment is not None:
            with suppress(Exception):
                environment.close()
