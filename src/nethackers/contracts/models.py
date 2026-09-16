from __future__ import annotations

from dataclasses import asdict, dataclass, fields
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

# NLE engine end-of-episode codes (nle StepStatus), recorded verbatim into a
# TrajectoryResult's `end_status` (as a string) by the arena:
#   "1" = the character died in-game                                    -> died
#  "-1" = the episode ended without a death or ascension               -> aborted
#         (quit / escaped / ran out the step budget; no cause of death)
#   "0" = still running (never terminal in a stored result)            -> running
# Ascension is tracked separately (the `ascended` flag), so a caller that has it
# should prefer it -- an ascension can still report end_status "1".
_NLE_END_STATUS = {"1": "died", "-1": "aborted", "0": "running"}


def end_status_word(end_status: str | int | None) -> str | None:
    """Translate a raw NLE ``end_status`` code to a human word (died / aborted /
    running) for display and briefs. Returns ``None`` for a ``None``/empty code;
    a value that is already a word (test fixtures, or a future arena that emits
    words) passes straight through unchanged."""
    if end_status is None or str(end_status) == "":
        return None
    code = str(end_status)
    return _NLE_END_STATUS.get(code, code)


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
    # Per-episode identity + furthest milestone reached (M2a). Defaulted --
    # not inserted earlier in the field list -- so M1's existing positional
    # TrajectoryResult(...) call sites and from_dict(old_dict) (via
    # cls(**value)) keep working unchanged; arena/trajectory.py populates
    # them for real starting in Task 2.
    character: str = ""
    milestone: str | None = None
    # Verbatim NetHack xlogfile `death=` string for a genuine death (else None:
    # bot failure, ascension, quit/escaped). Defaulted -- same backward-compat
    # pattern as character/milestone; populated by arena/trajectory.py._result.
    cause_of_death: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrajectoryResult:
        # Drop unknown keys so a newer client's extra evidence fields don't
        # break an older consumer (forward/version-skew tolerance).
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in value.items() if k in known})


@dataclass(frozen=True)
class Objective:
    character: str | None            # e.g. "val-dwa-law-fem"; None => NLE natural random draw
    max_steps: int = DEFAULT_MAX_STEPS
    no_progress_timeout: int = DEFAULT_NO_PROGRESS_TIMEOUT
    action_timeout_seconds: float = 120.0  # LOCAL hang-guard; see hub/objectives.py
    seed_set: str = "public-8"


@dataclass(frozen=True)
class ObjectiveSpec:
    """A grading functional over a *published, fixed* ``(seed, character)``
    batch (M2a §4): ``name``/``kind`` identify it in the hub's objective
    catalog, ``batch`` is the exact episode list ``eval`` must run so tier-1
    evidence is comparable, and ``aggregation`` (e.g. ``"asc_median_mean"``,
    ``"mean"``) says how atoms combine into one ranked score. Distinct from
    ``Objective``, which is per-trajectory run configuration (character/
    step/timeout knobs for a single episode)."""

    name: str
    kind: str
    batch: tuple[tuple[int, str], ...]
    max_steps: int
    no_progress_timeout: int
    action_timeout_seconds: float
    aggregation: str

    def characters(self) -> tuple[str, ...]:
        """The batch's characters, in published batch order."""
        return tuple(character for _seed, character in self.batch)


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


@dataclass(frozen=True)
class Atom:
    """One episode's stored, immutable result (M2a §3/§5): the hub's only
    substrate -- every derived view (attainment record, elite pool, boards)
    is computed from atoms. Flatter than ``Evidence``: it carries a
    ``solution_digest`` (a reference, not a nested object) plus
    ``owner``/``tier`` provenance, so a whole ``Evidence`` becomes one
    ``Atom`` per ``TrajectoryResult`` (``identity`` <- ``result.character``,
    ``milestone`` <- ``result.milestone``). No ``objective_digest``: after
    random/all's retirement (Task A1) every identity has exactly one
    canonical objective, so ``identity`` alone is the key."""

    solution_digest: str
    owner: str
    tier: str
    identity: str
    seed: int
    progression: float
    milestone: str | None
    ascended: bool
    status: ResultStatus
    turns: int
    steps: int
    evaluator_image: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Atom:
        return cls(**value)
