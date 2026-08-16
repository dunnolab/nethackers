"""Tests for ``nethackers.hubclient.frontier``: pure adapters that assemble
the two Frontier regimes' ``{identity: value}`` maps from hub reads
(``HubClient.elites``/``.board``/``.solution_frontier``, Task 2). Exercised
against a plain fake client -- no HTTP, no real ``HubClient`` -- since each
adapter only ever calls one named method on the client and never touches
anything else on it.
"""

from __future__ import annotations

import pytest

from nethackers.hubclient.frontier import champion, champion_scores, overall_mean, universe_scores

VAL_IDENTITY = "val-hum-neu-fem"
WIZ_IDENTITY = "wiz-elf-cha-mal"


class _FakeClient:
    """A minimal stand-in for ``HubClient``: ``elites``/``board``/
    ``solution_frontier`` ignore whatever arguments the adapter passes them
    and return the canned list the test scripted."""

    def __init__(self, *, elites=None, board=None, frontier=None):
        self._elites = elites if elites is not None else []
        self._board = board if board is not None else []
        self._frontier = frontier if frontier is not None else []

    def elites(self, objective):
        return self._elites

    def board(self, objective):
        return self._board

    def solution_frontier(self, digest):
        return self._frontier


# --- universe_scores: rank-1 elites spread ----------------------------------


def test_universe_scores_keeps_only_rank_1_rows():
    # Two identities, each with a rank-1 and a rank-2 row, deliberately out
    # of rank-major order -- the filter must key off each row's own `rank`
    # field, not assume rank-1 rows come first in the list.
    rows = [
        {"identity": WIZ_IDENTITY, "solution_digest": "sha256:b", "score": 0.2, "rank": 2},
        {"identity": VAL_IDENTITY, "solution_digest": "sha256:a", "score": 0.8, "rank": 1},
        {"identity": WIZ_IDENTITY, "solution_digest": "sha256:c", "score": 0.65, "rank": 1},
        {"identity": VAL_IDENTITY, "solution_digest": "sha256:d", "score": 0.5, "rank": 2},
    ]
    client = _FakeClient(elites=rows)

    result = universe_scores(client)

    assert result == {VAL_IDENTITY: 0.8, WIZ_IDENTITY: 0.65}


def test_universe_scores_casts_score_to_float():
    rows = [{"identity": VAL_IDENTITY, "solution_digest": "sha256:a", "score": 1, "rank": 1}]
    client = _FakeClient(elites=rows)

    result = universe_scores(client)

    assert result[VAL_IDENTITY] == 1.0
    assert isinstance(result[VAL_IDENTITY], float)


def test_universe_scores_empty_elites_returns_empty_map():
    client = _FakeClient(elites=[])

    assert universe_scores(client) == {}


# --- champion: board("random")[0] -------------------------------------------


def test_champion_returns_digest_and_owner_from_top_board_row():
    rows = [
        {"rank": 1, "solution_digest": "sha256:top", "owner": "sam"},
        {"rank": 2, "solution_digest": "sha256:second", "owner": "alex"},
    ]
    client = _FakeClient(board=rows)

    result = champion(client)

    assert result == ("sha256:top", "sam")


def test_champion_returns_none_for_empty_board():
    client = _FakeClient(board=[])

    assert champion(client) is None


# --- champion_scores: one solution's per-identity frontier ------------------


def test_champion_scores_maps_identity_to_progression():
    rows = [
        {"identity": VAL_IDENTITY, "progression": 0.42, "episodes": 3},
        {"identity": WIZ_IDENTITY, "progression": 0.77, "episodes": 5},
    ]
    client = _FakeClient(frontier=rows)

    result = champion_scores(client, "sha256:top")

    assert result == {VAL_IDENTITY: 0.42, WIZ_IDENTITY: 0.77}


def test_champion_scores_empty_frontier_returns_empty_map():
    client = _FakeClient(frontier=[])

    assert champion_scores(client, "sha256:top") == {}


# --- overall_mean: mean of the non-None values ------------------------------


def test_overall_mean_averages_present_values():
    assert overall_mean({"a": 0.2, "b": 0.6}) == pytest.approx(0.4)


def test_overall_mean_skips_none_values():
    assert overall_mean({"a": 0.2, "b": None, "c": 0.6}) == pytest.approx(0.4)


def test_overall_mean_empty_scores_returns_none():
    assert overall_mean({}) is None


def test_overall_mean_all_none_returns_none():
    assert overall_mean({"a": None, "b": None}) is None
