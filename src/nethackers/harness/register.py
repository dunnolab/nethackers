"""Record a validation-confirmed win to the hub (self-report).

The win must be a *real* public ``repo@commit`` so the hub's commit-exists
check passes and the program is fetchable -- the caller publishes the winning
worktree first (see ``harness.loop``'s ``publish`` hook) and passes the
resulting ``reference`` here. Registration carries the whole batch's
``Evidence`` (``tier="self-reported"``) in a single call; the hub slices it
into per-identity atoms server-side (``evidence_to_atoms`` keys each atom by
its own result's identity), so one solution row grows atoms across the whole
identity set.
"""
from __future__ import annotations

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
