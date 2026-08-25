"""Tests for the /baseline view: per-identity AutoAscend aggregates
(mean progression, deepest milestone, episode count) over baseline_atoms."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.baseline import read_baseline


def _a(ident, prog, milestone):
    # baseline_atoms has no UNIQUE constraint, so two rows may share seed=1.
    return Atom(solution_digest="autoascend", objective_digest="o", owner="autoascend",
                tier="baseline", identity=ident, seed=1, progression=prog, milestone=milestone,
                ascended=False, status="completed", turns=1, steps=1, evaluator_image="img")


def test_read_baseline_aggregates(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    store.insert_baseline_atoms([_a("val-dwa-law-fem", 0.10, "Dlvl:3"),
                                 _a("val-dwa-law-fem", 0.08, "Dlvl:2")])
    out = read_baseline(store)
    assert out["owner"] == "autoascend"
    cell = out["per_identity"]["val-dwa-law-fem"]
    assert abs(cell["progression"] - 0.09) < 1e-9
    assert cell["episodes"] == 2
    assert cell["deepest"] == "Dlvl:3"        # ranked by ACHIEVEMENTS, not string order
    assert abs(out["overall"] - 0.09) < 1e-9


def test_read_baseline_empty(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    assert read_baseline(store) == {"owner": "autoascend", "per_identity": {}, "overall": None}
