from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

from nethackers.contracts.models import TrajectoryResult


def aggregate(results: Iterable[TrajectoryResult]) -> dict[str, Any]:
    """Summarize a batch of trajectory results.

    Unlike the sibling's ``aggregate_results``, this takes no
    ``EvaluationManifest`` and performs no completeness check against an
    expected episode count -- a caller that needs that invariant enforces it
    itself before aggregating.
    """
    ordered = list(results)
    progress_values = [result.progress for result in ordered]
    return {
        "mean_progress": statistics.fmean(progress_values) if progress_values else 0.0,
        "median_progress": statistics.median(progress_values) if progress_values else 0.0,
        "ascensions": sum(1 for result in ordered if result.ascended),
        "status_counts": Counter(result.status for result in ordered),
    }
