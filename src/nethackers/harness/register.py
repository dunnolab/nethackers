"""Record a validation-confirmed win to the hub (trusted self-report)."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from nethackers.contracts.models import Evidence


def register_win(
    hub: Any,
    *,
    token: str,
    owner: str,
    child_manifest: dict[str, Any],
    evidence: Evidence,
    parent_digest: str,
) -> Any:
    commit = evidence.solution_digest.split(":", 1)[1][:40]
    reference = {"repo": f"github.com/{owner}/nethacker-runs", "commit": commit}
    manifest = {**child_manifest, "parents": [parent_digest]}
    return hub.register(
        token=token, reference=reference, manifest=manifest, evidence=evidence.to_dict()
    )


def _slice_evidence(evidence: Evidence, identity: str) -> Evidence:
    sliced = tuple(r for r in evidence.results if r.character == identity)
    return Evidence.from_results(
        solution_digest=evidence.solution_digest,
        objective=replace(evidence.objective, seed_set=identity),
        evaluator_image=evidence.evaluator_image,
        results=sliced,
        created_at=evidence.created_at,
        tier=evidence.tier,
    )


def register_win_slices(
    hub: Any,
    *,
    token: str,
    owner: str,
    child_manifest: dict[str, Any],
    evidence: Evidence,
    identities: list[str],
    parent_digest: str,
) -> list[Any]:
    """Register a generalist win as one ordinary per-identity win per member
    of S. Each slice's (seed, character) set equals that identity's published
    batch, so the hub's UnknownObjective/WrongBatch checks pass unchanged.
    One solution row; atoms across all of S."""
    return [
        register_win(hub, token=token, owner=owner, child_manifest=child_manifest,
                     evidence=_slice_evidence(evidence, ident), parent_digest=parent_digest)
        for ident in sorted(identities)
    ]
