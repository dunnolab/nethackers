"""Unit tests for the 2D spatial-navigation geometry (``tui.nav``)."""
from __future__ import annotations

import pytest

from nethackers.tui.nav import nearest_in_direction


class _Region:
    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.width, self.height = x, y, w, h


class _W:
    def __init__(self, name: str, x: int, y: int, w: int = 10, h: int = 5) -> None:
        self.name = name
        self.region = _Region(x, y, w, h)

    def __repr__(self) -> str:
        return self.name


# a Home-like 2x2 grid
A = _W("A", 0, 0)      # top-left
B = _W("B", 20, 0)     # top-right
C = _W("C", 0, 10)     # bottom-left
D = _W("D", 20, 10)    # bottom-right
GRID = [A, B, C, D]


def test_moves_to_the_neighbour_on_each_side():
    assert nearest_in_direction(A, GRID, "right") is B
    assert nearest_in_direction(A, GRID, "down") is C
    assert nearest_in_direction(B, GRID, "left") is A
    assert nearest_in_direction(D, GRID, "up") is B


def test_no_candidate_that_way_returns_none():
    assert nearest_in_direction(A, GRID, "left") is None
    assert nearest_in_direction(A, GRID, "up") is None


def test_prefers_axis_aligned_over_diagonally_closer():
    # from A, "down" must pick the aligned C, never the diagonal D
    assert nearest_in_direction(A, GRID, "down") is C
    # even if D were slightly nearer in raw distance, the perp penalty wins
    near_diag = _W("near_diag", 8, 9)   # closer by raw distance, but off-axis
    assert nearest_in_direction(A, [C, near_diag], "down") is C


def test_skips_unmounted_zero_region_widgets():
    ghost = _W("ghost", 20, 0, 0, 0)  # width/height 0 -> not laid out
    assert nearest_in_direction(A, [ghost], "right") is None


def test_origin_never_selects_itself():
    assert nearest_in_direction(A, [A], "right") is None


def test_bad_direction_raises():
    with pytest.raises(ValueError):
        nearest_in_direction(A, GRID, "sideways")
