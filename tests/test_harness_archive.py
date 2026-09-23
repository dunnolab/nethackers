from __future__ import annotations

import pytest

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.archive import UNION, CellArchive

IDS = ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]

def _ev(means: dict[str, float]) -> Evidence:
    results = tuple(
        TrajectoryResult(trajectory_id=i, status="completed", progress=v, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=c, milestone=None)
        for i, (c, v) in enumerate(means.items()))
    return Evidence.from_results(solution_digest="sha256:x",
                                 objective=Objective(character=None, seed_set="s"),
                                 evaluator_image="img", results=results, created_at="t")

def test_insert_seeds_all_cells_then_reports_improved(tmp_path):
    arc = CellArchive(IDS)
    seed_ev = _ev({IDS[0]: 0.2, IDS[1]: 0.2})
    improved = arc.insert("seed", tmp_path / "seed", seed_ev)
    # both fixtures below give full coverage, so the union cell rides along
    # (task 1: union tracks the best full-coverage program too).
    assert set(improved) == set(IDS) | {UNION}          # empty archive -> all seeded
    assert arc.cell(IDS[0]).score == 0.2

    # a child that beats only IDS[0] (but still raises the union mean)
    child_ev = _ev({IDS[0]: 0.9, IDS[1]: 0.1})
    improved = arc.insert("child", tmp_path / "child", child_ev)
    assert improved == [IDS[0], UNION]                    # best-per-cell winner + union
    assert arc.cell(IDS[0]).digest == "child"
    assert arc.cell(IDS[1]).digest == "seed"             # untouched loser

def test_coverage_counts_non_seed_cells(tmp_path):
    arc = CellArchive(IDS)
    seed_ev = _ev({IDS[0]: 0.2, IDS[1]: 0.2})
    arc.insert("seed", tmp_path / "seed", seed_ev)
    arc.mark_seed("seed")
    assert arc.coverage() == (0, 2)
    arc.insert("child", tmp_path / "child", _ev({IDS[0]: 0.9, IDS[1]: 0.1}))
    assert arc.coverage() == (1, 2)                      # one cell now holds a specialist

def test_union_cell_seeded_on_first_full_coverage(tmp_path):
    arc = CellArchive(IDS)  # 2 identities
    improved = arc.insert("gen", tmp_path / "gen", _ev({IDS[0]: 0.2, IDS[1]: 0.4}))
    assert UNION in improved                      # first full-union insert seeds the union cell
    assert arc.union is not None
    # pytest.approx, not ==: mean([0.2, 0.4]) is 0.30000000000000004 in
    # IEEE-754 float, not the exact decimal 0.3 (see test_harness_aggregate.py).
    assert arc.union.score == pytest.approx(0.3)  # union_mean([0.2, 0.4])
    assert arc.union.digest == "gen"

def test_union_only_win_registers(tmp_path):
    # The iter-12 recovery: specialists own both identity cells; a generalist
    # that raises the overall mean wins NOTHING today, but must take the union.
    arc = CellArchive(IDS)
    arc.insert("gen",   tmp_path / "gen",   _ev({IDS[0]: 0.5, IDS[1]: 0.5}))
    arc.insert("specA", tmp_path / "specA", _ev({IDS[0]: 0.9, IDS[1]: 0.1}))
    arc.insert("specB", tmp_path / "specB", _ev({IDS[0]: 0.1, IDS[1]: 0.9}))
    # cells: IDS[0]=0.9, IDS[1]=0.9; union incumbent = gen (0.5)
    improved = arc.insert("gen2", tmp_path / "gen2", _ev({IDS[0]: 0.6, IDS[1]: 0.6}))
    assert improved == [UNION]                     # beats the union, no single cell
    assert arc.union.digest == "gen2"
    assert arc.union.score == 0.6

def test_union_ignores_subunion_coldstart_evidence(tmp_path):
    arc = CellArchive(IDS)  # 2 identities
    improved = arc.insert("champA", tmp_path / "champA", _ev({IDS[0]: 0.9}))  # partial
    assert improved == [IDS[0]]                    # per-identity cell seeded
    assert arc.union is None                       # sub-union evidence must NOT set union

def test_size1_set_has_no_union_cell(tmp_path):
    arc = CellArchive([IDS[0]])
    improved = arc.insert("a", tmp_path / "a", _ev({IDS[0]: 0.5}))
    assert improved == [IDS[0]]
    assert UNION not in improved
    assert arc.union is None


def test_a_program_that_ties_a_cell_and_wins_elsewhere_takes_the_tied_cell(tmp_path):
    """The dominating bot holds the cell, even where it only tied.

    Run 20260922-224201: a change helped the orc rogues and left the human ones
    untouched, so the human cells kept the older, strictly worse bot. The loop
    hands out cell elites as parents, so three iterations in a row drew a human
    cell, got that stale tree, re-derived the orc change -- the one visible way
    to reach the stated target -- reproduced the champion exactly, and were
    rejected for improving nothing.
    """
    arc = CellArchive(IDS)
    arc.insert("first", tmp_path / "first", _ev({IDS[0]: 0.2, IDS[1]: 0.2}))

    # ties IDS[0] exactly, strictly better on IDS[1]
    improved = arc.insert("second", tmp_path / "second", _ev({IDS[0]: 0.2, IDS[1]: 0.5}))

    assert improved == [IDS[1], UNION]                 # the tie is NOT claimed as a win
    assert arc.cell(IDS[1]).digest == "second"
    assert arc.cell(IDS[0]).digest == "second"         # ... but it holds the tied cell
    assert arc.cell(IDS[0]).score == 0.2               # at the score it actually got


def test_a_program_that_only_ties_takes_nothing(tmp_path):
    """Dominance, not churn: tying everywhere wins nothing, so an iteration that
    reproduces an existing elite leaves the archive exactly as it was."""
    arc = CellArchive(IDS)
    arc.insert("first", tmp_path / "first", _ev({IDS[0]: 0.2, IDS[1]: 0.4}))

    improved = arc.insert("same", tmp_path / "same", _ev({IDS[0]: 0.2, IDS[1]: 0.4}))

    assert improved == []
    assert arc.cell(IDS[0]).digest == "first"
    assert arc.cell(IDS[1]).digest == "first"
    assert arc.union is not None and arc.union.digest == "first"
