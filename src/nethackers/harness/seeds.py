"""Dev (published, registerable) and validation (fresh, local-only) specs.

``validation`` is the evolver's own overfit gate -- reserved seeds the
evolver can see but the published dev batch doesn't cover. Distinct from
the (deferred, M2b) verifier's *held-out* tier, which is derived from a
secret key the evolver never sees at all."""
from __future__ import annotations

from nethackers.contracts.models import ObjectiveSpec
from nethackers.hub.objectives import CATALOG, build_union_spec
from nethackers.hub.selector import resolve


def dev_spec(objective_name: str) -> ObjectiveSpec:
    r = resolve(objective_name)
    if r.kind == "all":
        raise ValueError(
            "'all' is a leaderboard view, not an evolve target; "
            "to evolve across every identity use the glob '*'")
    if r.kind in ("single", "random"):
        return CATALOG[r.name]
    return build_union_spec(r.identities, name=r.name)


def validation_spec(
    objective_name: str, *, n: int, start: int = 1000, max_steps: int | None = None
) -> ObjectiveSpec:
    r = resolve(objective_name)
    dev = dev_spec(objective_name)
    if r.kind == "set":
        batch = tuple(
            (seed, ident)
            for ident in sorted(r.identities)
            for seed in range(start, start + n)
        )
    else:  # single / random: one character (today's behavior), fresh seeds
        character = dev.characters()[0]
        batch = tuple((seed, character) for seed in range(start, start + n))
    return ObjectiveSpec(
        name=f"{r.name}__validation",
        kind=dev.kind,
        batch=batch,
        max_steps=dev.max_steps if max_steps is None else max_steps,
        no_progress_timeout=dev.no_progress_timeout,
        action_timeout_seconds=dev.action_timeout_seconds,
        aggregation=dev.aggregation,
    )
