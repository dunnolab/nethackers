"""2D spatial keyboard navigation geometry for the dashboard.

Pure, unit-testable: given the cursor widget and the candidate widgets (each
exposing a Textual ``.region`` with ``x``/``y``/``width``/``height``), pick
the nearest candidate in a compass direction. The stateful modal driver
(Navigate vs Interact, Enter/Esc, live tab switching) lives on the App and
calls into this.

Scoring: only candidates strictly on the pressed side of the cursor's centre
are eligible; among those, distance along the travel axis dominates and
perpendicular offset is penalised (``* PERP_WEIGHT``) so a press keeps the
cursor roughly aligned instead of darting diagonally to a closer-but-sideways
element.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PERP_WEIGHT = 3.0
_EPS = 0.5  # a half-cell dead-zone so exactly-aligned edges don't self-match

_DIRECTIONS = ("up", "down", "left", "right")


def _center(region: Any) -> tuple[float, float]:
    return (region.x + region.width / 2.0, region.y + region.height / 2.0)


def dedup_visible(widgets: Iterable[Any]) -> list[Any]:
    """De-duplicate (by identity) and drop any widget whose ``.region`` is
    empty -- unmounted or not laid out, so it can't be a cursor target."""
    out: list[Any] = []
    seen: set[int] = set()
    for w in widgets:
        if id(w) in seen:
            continue
        seen.add(id(w))
        r = w.region
        if r.width > 0 and r.height > 0:
            out.append(w)
    return out


def nearest_in_direction(
    origin: Any, candidates: Iterable[Any], direction: str
) -> Any | None:
    """Return the candidate whose screen ``.region`` is nearest to ``origin``
    in ``direction`` (one of ``up``/``down``/``left``/``right``), or ``None``
    when nothing lies that way. ``origin`` is excluded implicitly (a zero
    on-axis delta fails the side test). Widgets with an empty region
    (unmounted / not laid out) are skipped."""
    if direction not in _DIRECTIONS:
        raise ValueError(f"bad direction: {direction!r}")
    ox, oy = _center(origin.region)
    best: Any = None
    best_score: float | None = None
    for w in candidates:
        r = w.region
        if r.width <= 0 or r.height <= 0:
            continue
        cx, cy = _center(r)
        dx, dy = cx - ox, cy - oy
        if direction == "right" and dx <= _EPS:
            continue
        if direction == "left" and dx >= -_EPS:
            continue
        if direction == "down" and dy <= _EPS:
            continue
        if direction == "up" and dy >= -_EPS:
            continue
        if direction in ("left", "right"):
            primary, perp = abs(dx), abs(dy)
        else:
            primary, perp = abs(dy), abs(dx)
        score = primary + perp * PERP_WEIGHT
        if best_score is None or score < best_score:
            best, best_score = w, score
    return best
