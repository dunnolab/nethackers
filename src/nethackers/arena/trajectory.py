from __future__ import annotations

import time
import traceback
from contextlib import suppress
from pathlib import Path

from nethackers.arena.environment import EnvironmentMetrics, make_environment
from nethackers.arena.sandbox import AgentClient, BotError, BotTimeout, InvalidAction
from nethackers.contracts.models import Objective, ResultStatus, TrajectoryResult, TrajectorySpec

_EMPTY_METRICS = EnvironmentMetrics(0.0, 0, 1, False, None, None)


def _result(
    trajectory_id: int,
    status: ResultStatus,
    metrics: EnvironmentMetrics,
    steps: int,
    started: float,
    character: str,
    error: str | None = None,
) -> TrajectoryResult:
    """Build a TrajectoryResult, zeroing progress/ascended/milestone on bot failures.

    Per the plan's Global Constraints ("bot errors/timeouts -> progress
    0.0"), a bot-attributable failure (invalid_action/bot_timeout/bot_error)
    always scores zero progress and no ascension, regardless of what the
    environment had recorded before the failure -- matching the sibling's
    ``_result()``. M2a extends the same zeroing to ``milestone`` (a bot
    failure gets no credited milestone either). ``character`` identifies
    which build was played, not how the episode ended, so it is always
    recorded as given -- it is never zeroed. ``completed``/
    ``trajectory_timeout``/``infrastructure_error`` use ``metrics`` as-is.
    """
    bot_failure = status in {"invalid_action", "bot_timeout", "bot_error"}
    return TrajectoryResult(
        trajectory_id=trajectory_id,
        status=status,
        progress=0.0 if bot_failure else metrics.progress,
        ascended=False if bot_failure else metrics.ascended,
        steps=steps,
        turns=metrics.turns,
        max_depth=metrics.max_depth,
        end_status=metrics.end_status,
        error=error[-8_000:] if error else None,
        wall_seconds=round(time.monotonic() - started, 6),
        character=character,
        milestone=None if bot_failure else metrics.milestone,
    )


def run_trajectory(
    submission_path: str | Path,
    spec: TrajectorySpec,
    objective: Objective,
    character: str = "",
) -> TrajectoryResult:
    """Run one trajectory: a fresh sandboxed bot against a fresh environment.

    Builds the environment from ``objective``, then builds the sandbox client
    from ``spec``/``objective`` and the environment's own ``action_count``
    (the action-space size isn't known until the environment exists), drives
    the reset/act/step loop until the episode ends, and always tears both
    down in a ``finally``. Any exception is mapped to a terminal
    ``ResultStatus`` instead of propagating, so a caller (e.g. a batch
    runner) can loop over many trajectories without a single bad episode
    aborting the run.

    ``character`` (M2a) is recorded on the result as-is via ``_result()``; it
    identifies which build was actually played for this trajectory. It is
    independent of ``objective.character``, which only configures the
    environment's NLE character selection (fixed build vs. natural random
    draw) -- callers pass the same value for both when they know the build
    upfront. Defaults to ``""`` (matching ``TrajectoryResult.character``'s
    own default) so existing callers keep working before they're updated to
    pass it explicitly.
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
            action_count=environment.action_count,
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
        return _result(
            spec.trajectory_id, "completed", environment.metrics(), steps, started, character
        )
    except InvalidAction as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(
            spec.trajectory_id, "invalid_action", metrics, steps, started, character, str(error)
        )
    except BotTimeout as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(
            spec.trajectory_id, "bot_timeout", metrics, steps, started, character, str(error)
        )
    except BotError as error:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(
            spec.trajectory_id, "bot_error", metrics, steps, started, character, str(error)
        )
    except Exception:
        metrics = environment.metrics() if environment is not None else _EMPTY_METRICS
        return _result(
            spec.trajectory_id,
            "infrastructure_error",
            metrics,
            steps,
            started,
            character,
            traceback.format_exc(limit=30),
        )
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
        if environment is not None:
            with suppress(Exception):
                environment.close()
