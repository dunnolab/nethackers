"""Shared milestone helper: the deepest achievement label in a set, by the
single ACHIEVEMENTS value scale (arena.progress). Used by every view that
reports a "deepest reach" (baseline, boards, aggregate) so the rule can't
drift between them."""

from __future__ import annotations

from collections.abc import Iterable

from nethackers.arena.progress import ACHIEVEMENTS


def deepest_milestone(milestones: Iterable[str | None]) -> str | None:
    ms = [m for m in milestones if m is not None]
    return max(ms, key=lambda m: ACHIEVEMENTS.get(m, 0.0)) if ms else None
