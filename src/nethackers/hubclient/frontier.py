"""Frontier data adapters: assemble the two regimes' {identity: value} maps
from hub reads, shared by the TUI MapView and the CLI frontier command."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def baseline_scores(client: Any) -> dict[str, float]:
    """AutoAscend's per-identity progression map from ``GET /baseline``."""
    payload = client.baseline() or {}
    return {
        str(identity): float(cell["progression"])
        for identity, cell in (payload.get("per_identity") or {}).items()
        if cell.get("progression") is not None
    }


def universe_scores(client: Any) -> dict[str, float]:
    """Each identity's #1 elite, keyed by identity -- ``client.elites
    ("generalist")`` is the "Universe" regime (all 73 identities, best
    program each), from the live ``/elites`` view."""
    out: dict[str, float] = {}
    for row in client.elites("generalist") or []:
        if int(row.get("rank", 0)) == 1:
            out[str(row["identity"])] = float(row["score"])
    return out


def champion(client: Any) -> tuple[str, str] | None:
    board = client.board("generalist") or []
    if not board:
        return None
    top = board[0]
    return str(top["program_id"]), str(top.get("owner", ""))


def champion_scores(client: Any, digest: str) -> dict[str, float]:
    return {
        str(r["identity"]): float(r["progression"])
        for r in (client.solution_frontier(digest) or [])
    }


def overall_mean(scores: Mapping[str, float | None]) -> float | None:
    values = [v for v in scores.values() if v is not None]
    return sum(values) / len(values) if values else None


def with_baseline_floor(
    scores: Mapping[str, float | None], baseline: Mapping[str, float | None]
) -> dict[str, float]:
    """Best-of-all scores, treating AutoAscend as a real competitor."""
    identities = scores.keys() | baseline.keys()
    return {
        identity: max(value for value in (scores.get(identity), baseline.get(identity))
                      if value is not None)
        for identity in identities
        if scores.get(identity) is not None or baseline.get(identity) is not None
    }
