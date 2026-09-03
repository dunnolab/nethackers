"""Verifier auth boundary for the verified tier (Task 4). ``VerifierConfig``
holds the hidden-eval secret, hidden seeds, and the set of tokens a verifier
node may present; ``resolve_verifier`` checks a presented token against that
set and returns its fingerprint (never the raw token) for storage/lookup.

Kept minimal -- later tasks (5/6/7) extend this module with the verified
submission/ingest routes that build on this auth boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class VerifierConfig:
    tokens: frozenset[str]
    secret: str
    seeds: tuple[int, ...]


class VerifierAuthError(Exception):
    """Presented token is not a configured verifier token."""


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_verifier(token: str, cfg: VerifierConfig) -> str:
    if token not in cfg.tokens:
        raise VerifierAuthError("unknown verifier token")
    return token_fingerprint(token)
