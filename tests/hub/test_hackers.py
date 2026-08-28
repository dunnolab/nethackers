"""Tests for ``nethackers.hub.views.hackers``: the Hackers union-frontier view.
A person's standing is the union of all their programs' best-per-identity over
a scope, scored per-component on each identity's own atoms (identity-keyed,
Task A3). Coverage-first ranking, no ``firsts``."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.hackers import hacker_board

VAL_IDS = ["val-dwa-law-fem", "val-hum-law-fem", "val-hum-neu-fem"]


def _new_store(tmp_path):
    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    return store


def _atom(**overrides):
    fields = dict(
        solution_digest="sha256:s",
        owner="sam", tier="self-reported", identity="val-dwa-law-fem", seed=0,
        progression=0.5, milestone=None, ascended=False, status="completed",
        turns=5, steps=10, evaluator_image="img@sha256:x",
    )
    fields.update(overrides)
    return Atom(**fields)


def _seed(store, atoms):
    for digest in {a.solution_digest for a in atoms}:
        store.upsert_solution(digest, repo="r", commit_sha="c", owner="sam",
                              root=".", entrypoint="bot.py", registered_at="t")
    store.insert_atoms(atoms)


def test_hacker_board_unions_best_per_identity_across_a_persons_solutions(tmp_path):
    store = _new_store(tmp_path)
    i0, i1 = VAL_IDS[0], VAL_IDS[1]
    atoms = [
        _atom(solution_digest="sha256:d1", owner="dun",
              identity=i0, seed=0, progression=0.4),
        _atom(solution_digest="sha256:d2", owner="dun",  # best on i0
              identity=i0, seed=0, progression=0.6),
        _atom(solution_digest="sha256:d1", owner="dun",
              identity=i1, seed=0, progression=0.5),
        _atom(solution_digest="sha256:a1", owner="ako",
              identity=i0, seed=0, progression=0.3),
    ]
    _seed(store, atoms)

    rows = hacker_board(store, VAL_IDS)

    by_owner = {r["owner"]: r for r in rows}
    assert by_owner["dun"]["coverage"] == 2                        # i0 and i1
    assert abs(by_owner["dun"]["mean_progression"] - 0.55) < 1e-9  # best(0.6 on i0), 0.5 on i1
    assert by_owner["ako"]["coverage"] == 1
    assert [r["owner"] for r in rows] == ["dun", "ako"]            # coverage-first
    assert rows[0]["total"] == 3


def test_hacker_board_empty_when_no_atoms(tmp_path):
    store = _new_store(tmp_path)
    assert hacker_board(store, VAL_IDS) == []
