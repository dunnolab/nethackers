"""Pure per-identity aggregation over an evaluation's results, for a
generalist objective's floor/coverage reporting. The accept/reject gate keeps
using evidence.mean_progress (== union_mean here); these add the per-identity
view the brief and the monitor surface."""
from __future__ import annotations

from collections.abc import Sequence
from statistics import mean
from typing import Protocol


class _HasCharProgress(Protocol):
    character: str
    progress: float


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
