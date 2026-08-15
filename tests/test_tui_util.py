"""Tests for tui._util's shared helpers. `_rows_in_order`'s coverage was
originally pinned in test_tui_app.py against tui.app's own (pre-Task-10)
copy; it moved here when NetHackersApp's shell cutover deleted app.py's
local copies in favor of these canonical ones (`_guarded`/`_slug` are
exercised indirectly through tui.screens.evolve.EvolveScreen's own tests)."""
from __future__ import annotations

from nethackers.tui._util import _rows_in_order


def test_rows_in_order_sorts_by_index_regardless_of_arrival():
    rows = {}
    for idx in (2, 0, 1):  # out-of-order completion
        rows[idx] = {"index": idx, "seed": 100 + idx, "progress": 0.1 * idx}
    ordered = _rows_in_order(rows)
    assert [r["index"] for r in ordered] == [0, 1, 2]
