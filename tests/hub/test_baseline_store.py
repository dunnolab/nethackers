"""Tests for the baseline_atoms store methods: AutoAscend's computed reference
atoms live in their own table, isolated from the participant `atoms` table so
they never enter boards/elites."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store


def _atom(ident="val-dwa-law-fem", prog=0.089):
    return Atom(solution_digest="autoascend", objective_digest="o1", owner="autoascend",
                tier="baseline", identity=ident, seed=1, progression=prog, milestone="Dlvl:3",
                ascended=False, status="completed", turns=1, steps=1, evaluator_image="img")


def test_baseline_atoms_roundtrip_and_isolation(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    n = store.insert_baseline_atoms([_atom(), _atom(ident="wiz-elf-cha-mal", prog=0.05)])
    assert n == 2
    got = store.iter_baseline_atoms(identity="val-dwa-law-fem")
    assert len(got) == 1
    assert abs(got[0].progression - 0.089) < 1e-9
    assert got[0].ascended is False  # bool, not int 0 -- mirrors iter_atoms
    # isolation: baseline atoms are NOT in the participant atoms table
    assert store.conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0] == 0


def test_iter_baseline_atoms_rejects_unknown_filter(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    try:
        store.iter_baseline_atoms(bogus="x")
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown filter key")
