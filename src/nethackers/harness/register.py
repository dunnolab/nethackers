"""Record a validation-confirmed win to the hub (trusted self-report)."""
from __future__ import annotations

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
