from __future__ import annotations

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.archive import CellArchive

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
    assert set(improved) == set(IDS)                    # empty archive -> all seeded
    assert arc.cell(IDS[0]).score == 0.2

    # a child that beats only IDS[0]
    child_ev = _ev({IDS[0]: 0.9, IDS[1]: 0.1})
    improved = arc.insert("child", tmp_path / "child", child_ev)
    assert improved == [IDS[0]]                          # best-per-cell, only the winner
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
