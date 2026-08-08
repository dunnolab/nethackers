from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Literal

DEFAULT_MAX_STEPS = 1_000_000
DEFAULT_NO_PROGRESS_TIMEOUT = 10_000

ResultStatus = Literal[
    "completed",
    "bot_error",
    "bot_timeout",
    "invalid_action",
    "trajectory_timeout",
    "infrastructure_error",
]


@dataclass(frozen=True)
class TrajectorySpec:
    trajectory_id: int
    core_seed: int
    display_seed: int
    level_seed: int
    bot_seed: int


@dataclass(frozen=True)
class TrajectoryResult:
    trajectory_id: int
    status: ResultStatus
    progress: float
    ascended: bool
    steps: int
    turns: int
    max_depth: int
    end_status: str | None
    error: str | None
    wall_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrajectoryResult:
        return cls(**value)


@dataclass(frozen=True)
class Objective:
    character: str | None            # e.g. "val-dwa-law-fem"; None => NLE natural random draw
    max_steps: int = DEFAULT_MAX_STEPS
    no_progress_timeout: int = DEFAULT_NO_PROGRESS_TIMEOUT
    action_timeout_seconds: float = 5.0
    seed_set: str = "public-8"

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Evidence:
    solution_digest: str
    objective: Objective
    evaluator_image: str
    tier: str
    results: tuple[TrajectoryResult, ...]
    episodes: int
    mean_progress: float
    ascensions: int
    created_at: str

    @classmethod
    def from_results(cls, *, solution_digest, objective, evaluator_image, results, created_at,
                     tier="self-reported"):
        results = tuple(results)
        return cls(
            solution_digest=solution_digest, objective=objective, evaluator_image=evaluator_image,
            tier=tier, results=results, episodes=len(results),
            mean_progress=mean(r.progress for r in results) if results else 0.0,
            ascensions=sum(1 for r in results if r.ascended), created_at=created_at)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["objective"] = asdict(self.objective)
        d["results"] = [r.to_dict() for r in self.results]
        return d

    @classmethod
    def from_dict(cls, value: dict) -> Evidence:
        v = dict(value)
        v["objective"] = Objective(**v["objective"])
        v["results"] = tuple(TrajectoryResult.from_dict(r) for r in v["results"])
        return cls(**v)
