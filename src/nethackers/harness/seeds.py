"""Dev (published, registerable) and validation (fresh, local-only) specs.

``validation`` is the evolver's own overfit gate -- reserved seeds the
evolver can see but the published dev batch doesn't cover. Distinct from
the (deferred, M2b) verifier's *held-out* tier, which is derived from a
secret key the evolver never sees at all."""
from __future__ import annotations

from nethackers.contracts.models import ObjectiveSpec
from nethackers.hub.objectives import CATALOG


def dev_spec(objective_name: str) -> ObjectiveSpec:
    return CATALOG[objective_name]


def validation_spec(
    objective_name: str, *, n: int, start: int = 1000, max_steps: int | None = None
) -> ObjectiveSpec:
    dev = dev_spec(objective_name)
    character = dev.characters()[0]
    batch = tuple((seed, character) for seed in range(start, start + n))
    return ObjectiveSpec(
        name=f"{objective_name}__validation",
        kind=dev.kind,
        batch=batch,
        max_steps=dev.max_steps if max_steps is None else max_steps,
        no_progress_timeout=dev.no_progress_timeout,
        action_timeout_seconds=dev.action_timeout_seconds,
        aggregation=dev.aggregation,
    )
