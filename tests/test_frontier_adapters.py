"""Tests for ``nethackers.hubclient.frontier``: pure adapters that assemble
the two Frontier regimes' ``{identity: value}`` maps from hub reads
(``HubClient.elites``/``.board``/``.program_identities``). Exercised against
a plain fake client -- no HTTP, no real ``HubClient`` -- since each adapter
only ever calls one named method on the client and never touches anything
else on it.
"""

from __future__ import annotations

import pytest

from nethackers.hubclient.frontier import (
    baseline_scores,
    champion,
    champion_scores,
    overall_mean,
    universe_scores,
    with_baseline_floor,
)

VAL_IDENTITY = "val-hum-neu-fem"
WIZ_IDENTITY = "wiz-elf-cha-mal"


class _FakeClient:
    """A minimal stand-in for ``HubClient``: ``elites``/``board``/
    ``program_identities`` ignore whatever arguments the adapter passes them
    and return the canned list the test scripted."""

    def __init__(self, *, elites=None, board=None, frontier=None, baseline=None):
        self._elites = elites if elites is not None else []
        self._board = board if board is not None else []
        self._frontier = frontier if frontier is not None else []
        self._baseline = baseline if baseline is not None else {}

    def elites(self, objective):
        return self._elites

    def board(self, scope):
        return self._board

    def program_identities(self, program_id):
        return self._frontier

    def baseline(self):
        return self._baseline


def test_baseline_scores_extracts_per_identity_progression():
    client = _FakeClient(baseline={
        "owner": "autoascend",
        "per_identity": {
            VAL_IDENTITY: {"progression": 0.4, "episodes": 2},
            WIZ_IDENTITY: {"progression": None, "episodes": 0},
        },
        "overall": 0.4,
    })

    assert baseline_scores(client) == {VAL_IDENTITY: 0.4}


def test_with_baseline_floor_takes_best_value_per_identity():
    assert with_baseline_floor(
        {VAL_IDENTITY: 0.3, WIZ_IDENTITY: 0.7},
        {VAL_IDENTITY: 0.5},
    ) == {VAL_IDENTITY: 0.5, WIZ_IDENTITY: 0.7}


# --- universe_scores: rank-1 elites spread ----------------------------------


def test_universe_scores_keeps_only_rank_1_rows():
    # Two identities, each with a rank-1 and a rank-2 row, deliberately out
    # of rank-major order -- the filter must key off each row's own `rank`
    # field, not assume rank-1 rows come first in the list.
    rows = [
        {"identity": WIZ_IDENTITY, "program_id": "prog_b", "score": 0.2, "rank": 2},
        {"identity": VAL_IDENTITY, "program_id": "prog_a", "score": 0.8, "rank": 1},
        {"identity": WIZ_IDENTITY, "program_id": "prog_c", "score": 0.65, "rank": 1},
        {"identity": VAL_IDENTITY, "program_id": "prog_d", "score": 0.5, "rank": 2},
    ]
    client = _FakeClient(elites=rows)

    result = universe_scores(client)

    assert result == {VAL_IDENTITY: 0.8, WIZ_IDENTITY: 0.65}


def test_universe_scores_casts_score_to_float():
    rows = [{"identity": VAL_IDENTITY, "program_id": "prog_a", "score": 1, "rank": 1}]
    client = _FakeClient(elites=rows)

    result = universe_scores(client)

    assert result[VAL_IDENTITY] == 1.0
    assert isinstance(result[VAL_IDENTITY], float)


def test_universe_scores_empty_elites_returns_empty_map():
    client = _FakeClient(elites=[])

    assert universe_scores(client) == {}


def test_universe_scores_reads_the_generalist_elites_not_all():
    # Universe = scope=generalist (all 73, best program each) -- the old
    # objective="all" token is retired.
    seen = []

    class _Spy:
        def elites(self, scope):
            seen.append(scope)
            return []

    universe_scores(_Spy())

    assert seen == ["generalist"]


# --- champion: board("generalist")[0] ----------------------------------------


def test_champion_returns_program_id_and_owner_from_top_board_row():
    rows = [
        {"rank": 1, "program_id": "prog_top", "owner": "sam"},
        {"rank": 2, "program_id": "prog_second", "owner": "alex"},
    ]
    client = _FakeClient(board=rows)

    result = champion(client)

    assert result == ("prog_top", "sam")


def test_champion_returns_none_for_empty_board():
    client = _FakeClient(board=[])

    assert champion(client) is None


def test_champion_reads_the_generalist_board_not_random():
    seen = []

    class _Spy:
        def board(self, objective):
            seen.append(objective)
            return [{"rank": 1, "program_id": "prog_top", "owner": "sam"}]

    result = champion(_Spy())

    assert result == ("prog_top", "sam")
    assert seen == ["generalist"]


# --- champion_scores: one program's per-identity frontier -------------------


def test_champion_scores_maps_identity_to_progression():
    rows = [
        {"identity": VAL_IDENTITY, "progression": 0.42, "episodes": 3},
        {"identity": WIZ_IDENTITY, "progression": 0.77, "episodes": 5},
    ]
    client = _FakeClient(frontier=rows)

    result = champion_scores(client, "prog_top")

    assert result == {VAL_IDENTITY: 0.42, WIZ_IDENTITY: 0.77}


def test_champion_scores_empty_frontier_returns_empty_map():
    client = _FakeClient(frontier=[])

    assert champion_scores(client, "prog_top") == {}


def test_champion_scores_reads_program_identities_not_the_retired_solution_frontier():
    # Forward-carry closure (Task 1 -> Task 5): champion() already returns a
    # program_id; champion_scores() must resolve it via program_identities()
    # (-> /programs/{id}/identities), never the retired solution_frontier()
    # (-> /solutions/{digest}/frontier, which 404s on a program_id).
    seen = []

    class _Spy:
        def program_identities(self, program_id):
            seen.append(program_id)
            return []

    champion_scores(_Spy(), "prog_top")

    assert seen == ["prog_top"]


# --- overall_mean: mean of the non-None values ------------------------------


def test_overall_mean_averages_present_values():
    assert overall_mean({"a": 0.2, "b": 0.6}) == pytest.approx(0.4)


def test_overall_mean_skips_none_values():
    assert overall_mean({"a": 0.2, "b": None, "c": 0.6}) == pytest.approx(0.4)


def test_overall_mean_empty_scores_returns_none():
    assert overall_mean({}) is None


def test_overall_mean_all_none_returns_none():
    assert overall_mean({"a": None, "b": None}) is None
