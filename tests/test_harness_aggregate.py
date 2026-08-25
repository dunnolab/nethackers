from dataclasses import dataclass

import pytest

from nethackers.contracts.models import TrajectoryResult
from nethackers.harness import aggregate as A
from nethackers.harness.aggregate import outcome_summary


@dataclass
class R:  # minimal stand-in for TrajectoryResult
    character: str
    progress: float


def test_per_identity_means_groups_by_character():
    res = [R("a", 0.2), R("a", 0.4), R("b", 0.9)]
    # pytest.approx, not ==: mean([0.2, 0.4]) is 0.30000000000000004 in
    # IEEE-754 float, not the exact decimal 0.3.
    assert A.per_identity_means(res) == pytest.approx({"a": 0.3, "b": 0.9})


def test_union_mean_is_pooled_mean():
    res = [R("a", 0.2), R("a", 0.4), R("b", 0.9)]
    assert abs(A.union_mean(res) - (0.2 + 0.4 + 0.9) / 3) < 1e-9


def test_floor_is_min_identity():
    assert A.floor({"a": 0.3, "b": 0.9}) == ("a", 0.3)
    assert A.floor({}) is None


def test_coverage_counts_present_over_total():
    assert A.coverage({"a": 0.3}, ["a", "b", "c"]) == (1, 3)


def test_regressions_worst_first_only_drops():
    parent = {"a": 0.5, "b": 0.5, "c": 0.5}
    child = {"a": 0.4, "b": 0.6, "c": 0.2}  # a down .1, c down .3, b up
    result = A.regressions(parent, child)
    # Identities + order are exact; deltas via pytest.approx (0.4 - 0.5 is
    # -0.09999999999999998 in IEEE-754 float, not the exact decimal -0.1).
    assert [ident for ident, _delta in result] == ["c", "a"]
    assert [delta for _ident, delta in result] == pytest.approx([-0.3, -0.1])


def test_regressions_eps_tolerance():
    assert A.regressions({"a": 0.50}, {"a": 0.49}, eps=0.02) == []


def _r(progress, end="died", milestone=None, depth=1):
    return TrajectoryResult(trajectory_id=0, status="completed", progress=progress,
        ascended=False, steps=1, turns=1, max_depth=depth, end_status=end, error=None,
        wall_seconds=0.1, character="c", milestone=milestone)


def test_outcome_summary_tallies_and_is_best_effort():
    s = outcome_summary([_r(0.1, "died"), _r(0.1, "died"), _r(0.2, "starved")])
    # mean-ish present, no crash on missing milestone
    assert "died×2" in s and "starved×1" in s and "0.1" in s
