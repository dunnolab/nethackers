"""Frontier data adapters: assemble the two regimes' {identity: value} maps
from hub reads, shared by the TUI MapView and the CLI frontier command."""
from __future__ import annotations

from typing import Any


def universe_scores(client: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in client.elites("all") or []:
        if int(row.get("rank", 0)) == 1:
            out[str(row["identity"])] = float(row["score"])
    return out


def champion(client: Any) -> tuple[str, str] | None:
    board = client.board("random") or []
    if not board:
        return None
    top = board[0]
    return str(top["solution_digest"]), str(top.get("owner", ""))


def champion_scores(client: Any, digest: str) -> dict[str, float]:
    return {
        str(r["identity"]): float(r["progression"])
        for r in (client.solution_frontier(digest) or [])
    }


def overall_mean(scores: dict[str, float | None]) -> float | None:
    values = [v for v in scores.values() if v is not None]
    return sum(values) / len(values) if values else None
