from dataclasses import dataclass

import pytest

from nethackers.contracts.models import TrajectoryResult
from nethackers.harness import aggregate as A
from nethackers.harness.aggregate import end_status_word, outcome_summary


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


def _r(progress, end="died", milestone=None, depth=1, asc=False):
    return TrajectoryResult(trajectory_id=0, status="completed", progress=progress,
        ascended=asc, steps=1, turns=1, max_depth=depth, end_status=end, error=None,
        wall_seconds=0.1, character="c", milestone=milestone)


def test_outcome_summary_tallies_and_is_best_effort():
    s = outcome_summary([_r(0.1, "died"), _r(0.1, "died"), _r(0.2, "starved")])
    # mean-ish present, no crash on missing milestone
    assert "died×2" in s and "starved×1" in s and "0.1" in s


def test_outcome_summary_translates_raw_nle_codes_and_is_ascension_aware():
    # real arena results carry the raw NLE end_status CODE (1=death, -1=aborted),
    # not a word -- the rollup the mutator reads must show words, and an ascension
    # must read "ascended", not "died" (its engine code can still be "1").
    s = outcome_summary([_r(0.1, "1"), _r(0.1, "1"), _r(0.3, "-1"),
                         _r(1.0, "1", asc=True)])
    assert "died×2" in s and "aborted×1" in s and "ascended×1" in s
    assert "1×" not in s and "-1×" not in s   # no raw codes leak to the brief


def test_end_status_word_translates_nle_codes_and_passes_words_through():
    # arena stores the raw NLE StepStatus code as a string; the shared helper
    # the monitor and the mutator brief both use maps it to a human word. Lives
    # in the harness (not contracts) so it stays out of the mutator image inputs.
    assert end_status_word("1") == "died"
    assert end_status_word("-1") == "aborted"
    assert end_status_word("0") == "running"
    assert end_status_word(1) == "died"              # tolerant of an int code
    assert end_status_word(None) is None             # no outcome recorded
    assert end_status_word("") is None
    assert end_status_word("died") == "died"         # already a word -> passthrough
    assert end_status_word("7") == "7"               # unknown code -> as-is, never crash
