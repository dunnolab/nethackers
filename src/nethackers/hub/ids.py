"""Opaque, slash-free program identifiers, plus the reserved constants
naming AutoAscend -- the reference floor.

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


# The reserved owner/solution key for AutoAscend, the reference floor. It is
# deliberately NOT a ``repo@commit`` and never gets a ``solutions`` row: the
# floor is what participants are measured against, never a participant itself.
# Shared by the public-seed baseline (``baseline_compute``) and the hidden-seed
# baseline (``verify.register_verified_baseline``) so the two can never drift.
AUTOASCEND_ID = "autoascend"

# Where AutoAscend lives in a repo checkout. Shared so the public-seed floor
# (``baseline_compute``) and the hidden-seed floor (``worker.verify``) are
# always computed from the same tree -- two paths that drifted apart would
# produce two floors that silently disagree. This module stays
# dependency-free (stdlib only), so both layers can import it.
AUTOASCEND_TREE = "roots/autoascend"
