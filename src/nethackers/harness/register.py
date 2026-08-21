"""M1: the evolve loop no longer auto-publishes wins to the hub.

Under the link-registry model (see the remote-hub + GitHub-login design) a hub
submission is a real public ``repo@commit`` the caller owns -- which an
in-progress evolve worktree does not have. So a validation-confirmed win is
accepted as the new *local* elite; publishing to the hub is a deliberate,
manual ``nethackers register --repo <repo> --commit <sha>`` step.

``register_win`` is kept as a no-op (its ``hub``/``token``/``evidence`` params
retained) so the loop's call site is untouched and M2 can restore real
auto-publishing -- of a genuine link -- without re-plumbing the loop.
"""
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
) -> None:
    """No-op in M1 -- hub auto-publishing is disabled (see module docstring).

    The evolve loop still accepts the win as its new local elite; the hub is
    intentionally not contacted, because a link-only submission needs a real
    ``repo@commit`` the owner controls, which the in-progress worktree is not.
    """
    return None
