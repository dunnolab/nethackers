"""Record a validation-confirmed win to the hub (self-report).

The win must be a *real* public ``repo@commit`` so the hub's commit-exists
check passes and the program is fetchable -- the caller publishes the winning
worktree first (see ``harness.loop``'s ``publish`` hook) and passes the
resulting ``reference`` here. Registration carries the per-identity ``Evidence``
(``tier="self-reported"``); the hub turns it into per-identity atoms. A
generalist win registers one single-identity slice per member of ``S``: one
solution row, atoms across the whole set.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from nethackers.contracts.models import Evidence


def register_win(
    hub: Any,
    *,
    token: str,
    child_manifest: dict[str, Any],
    evidence: Evidence,
    parent_digest: str,
    reference: dict[str, str],
) -> Any:
    """Register one win at the published ``reference`` (``{repo, commit}``)."""
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
    child_manifest: dict[str, Any],
    evidence: Evidence,
    identities: list[str],
    parent_digest: str,
    reference: dict[str, str],
) -> list[Any]:
    """Register a generalist win as one ordinary per-identity win per member
    of S, all at the same published ``reference``. Each slice's (seed, character)
    set equals that identity's published batch, so the hub's
    UnknownObjective/WrongBatch checks pass unchanged. One solution row; atoms
    across all of S."""
    return [
        register_win(hub, token=token, child_manifest=child_manifest,
                     evidence=_slice_evidence(evidence, ident),
                     parent_digest=parent_digest, reference=reference)
        for ident in sorted(identities)
    ]
