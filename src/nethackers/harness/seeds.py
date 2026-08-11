"""Dev (published, registerable) and held-out (fresh, local-only) specs."""
from __future__ import annotations

from nethackers.contracts.models import ObjectiveSpec
from nethackers.hub.objectives import CATALOG


def dev_spec(objective_name: str) -> ObjectiveSpec:
    return CATALOG[objective_name]


def heldout_spec(
    objective_name: str, *, n: int, start: int = 1000, max_steps: int | None = None
) -> ObjectiveSpec:
    dev = dev_spec(objective_name)
    character = dev.characters()[0]
    batch = tuple((seed, character) for seed in range(start, start + n))
    return ObjectiveSpec(
        name=f"{objective_name}__heldout",
        kind=dev.kind,
        batch=batch,
        max_steps=dev.max_steps if max_steps is None else max_steps,
        no_progress_timeout=dev.no_progress_timeout,
        action_timeout_seconds=dev.action_timeout_seconds,
        aggregation=dev.aggregation,
    )
