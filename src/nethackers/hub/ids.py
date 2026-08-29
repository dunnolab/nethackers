"""Opaque, slash-free program identifiers.

An ``id`` is ``"prog_" + sha256(reference)[:32]`` (128 bits), where
``reference`` is the ``f"{repo}@{commit}"`` string -- which is exactly what
``solutions.digest`` already stores, so this same function backfills the
migration (Task 3). Deterministic, so re-registering the same commit yields
the same id; opaque, so an external repo link is never the public key.
"""
from __future__ import annotations

import hashlib


def program_id(reference: str) -> str:
    return "prog_" + hashlib.sha256(reference.encode()).hexdigest()[:32]
