"""Pure per-identity aggregation over an evaluation's results, for a
generalist objective's floor/coverage reporting. The accept/reject gate keeps
using evidence.mean_progress (== union_mean here); these add the per-identity
view the brief and the monitor surface."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from statistics import mean
from typing import Protocol

# NLE engine end-of-episode codes (nle StepStatus), recorded verbatim into a
# TrajectoryResult's `end_status` (as a string) by the arena:
#   "1" = the character died in-game                                    -> died
#  "-1" = the episode ended without a death or ascension               -> aborted
#         (quit / escaped / ran out the step budget; no cause of death)
#   "0" = still running (never terminal in a stored result)            -> running
# Ascension is tracked separately (the `ascended` flag), so a caller that has it
# should prefer it -- an ascension can still report end_status "1".
#
# This lives here (host-side harness), deliberately NOT in contracts.models
# where `end_status` is defined: contracts/ is COPYd into the mutator image
# (image_inputs.MUTATOR_INPUT_PATHS), so any edit there churns the mutator's
# content fingerprint and invalidates its pinned build. This is a pure
# display/brief helper the sandbox never runs -- keeping it out of contracts
# leaves the pinned mutator image valid. The TUI (tui.run) imports it from here.
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


class _HasCharProgress(Protocol):
    # read-only properties (not bare attrs) so a frozen dataclass like
    # TrajectoryResult satisfies the protocol covariantly (mypy invariance fix).
    @property
    def character(self) -> str: ...
    @property
    def progress(self) -> float: ...


class _HasOutcome(Protocol):
    # read-only properties, matching TrajectoryResult's shape (mypy invariance
    # fix, same rationale as _HasCharProgress); end_status/milestone are
    # Optional on the model itself (older evidence, non-"completed" statuses),
    # so outcome_summary must tolerate None throughout -- best-effort, never
    # a crash on missing fields (spec §7).
    @property
    def progress(self) -> float: ...
    @property
    def ascended(self) -> bool: ...
    @property
    def end_status(self) -> str | None: ...
    @property
    def max_depth(self) -> int: ...
    @property
    def milestone(self) -> str | None: ...


def per_identity_means(results: Sequence[_HasCharProgress]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for r in results:
        buckets.setdefault(r.character, []).append(float(r.progress))
    return {ident: mean(vals) for ident, vals in buckets.items()}


def union_mean(results: Sequence[_HasCharProgress]) -> float:
    vals = [float(r.progress) for r in results]
    return mean(vals) if vals else 0.0


def floor(means: dict[str, float]) -> tuple[str, float] | None:
    if not means:
        return None
    ident = min(means, key=lambda k: means[k])
    return ident, means[ident]


def coverage(means: dict[str, float], identities: Sequence[str]) -> tuple[int, int]:
    total = len(identities)
    present = sum(1 for i in identities if i in means)
    return present, total


def regressions(
    parent: dict[str, float], child: dict[str, float], *, eps: float = 0.0
) -> list[tuple[str, float]]:
    drops = [
        (ident, child[ident] - parent[ident])
        for ident in parent
        if ident in child and child[ident] < parent[ident] - eps
    ]
    return sorted(drops, key=lambda t: t[1])  # most-negative first


def outcome_summary(results: Sequence[_HasOutcome]) -> str:
    """Best-effort text rollup of an eval's outcomes for `/refs/CONTEXT.md` /
    the brief, e.g. ``"died×4, aborted×1; deepest milestone: <m>; mean
    0.11"``. The outcome tally and progress mean are always computed; the
    deepest-milestone clause is appended only when at least one episode
    reports one. Never raises on missing/older-evidence fields (design §7) --
    an empty `results`, or every `milestone`/`end_status` being None, still
    renders, just without that clause.

    Outcomes are word-form and ascension-aware, matching the monitor's status
    column: an ascension reads "ascended" regardless of its raw engine code;
    otherwise the NLE ``end_status`` code is translated to a word
    (1->died, -1->aborted). Episodes with no recorded outcome are skipped."""
    if not results:
        return "no results"

    def _outcome(r: _HasOutcome) -> str | None:
        return "ascended" if r.ascended else end_status_word(r.end_status)

    tally = Counter(w for r in results if (w := _outcome(r)) is not None)
    parts = [f"{status}×{count}" for status, count in tally.most_common()]
    pieces = [", ".join(parts)] if parts else ["no outcome data"]
    milestoned = [r for r in results if r.milestone]
    if milestoned:
        deepest = max(milestoned, key=lambda r: r.max_depth)
        pieces.append(f"deepest milestone: {deepest.milestone}")
    pieces.append(f"mean {mean(r.progress for r in results):.2f}")
    return "; ".join(pieces)
