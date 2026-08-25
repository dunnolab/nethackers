"""Tests for nethackers.hub.views.progress: the website's best-so-far progress
time series. `frontier` is the running max over programs of each program's mean
progression on the objective (the leaderboard's "best mean", tracked over
time); `ascensions`/`coverage` are cumulative."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.views.progress import read_progress

IDENT = "val-dwa-law-fem"


def _atom(digest, seed, prog, asc=False, ident=IDENT, milestone="Dlvl:5"):
    # objective_digest wired to a real CATALOG entry (FK to objectives);
    # distinct seeds keep UNIQUE(solution_digest, objective_digest, seed) clear.
    return Atom(solution_digest=digest, objective_digest=CATALOG[ident].digest(),
                owner="a", tier="self-reported", identity=ident, seed=seed,
                progression=prog, milestone=milestone, ascended=asc,
                status="completed", turns=1, steps=1, evaluator_image="img")


def _seed_objective(store, ident=IDENT):
    store.objectives_upsert(CATALOG[ident])


def _backdate(store, day_by_seed):
    for seed, day in day_by_seed.items():
        store.conn.execute("UPDATE atoms SET created_at=? WHERE seed=?",
                           (f"{day}T04:00:00+00:00", seed))
    store.conn.commit()


def test_progress_frontier_is_top_program_mean_best_so_far(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    _seed_objective(store)
    store.upsert_solution(digest="s1", repo="github.com/a/b", commit_sha="c1",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    store.upsert_solution(digest="s2", repo="github.com/a/b", commit_sha="c2",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    # Program s1 lands day 23: one episode, mean 0.10.
    # Program s2 lands day 24: two episodes (0.30, 0.20), mean 0.25 -> beats s1.
    store.insert_atoms([
        _atom("s1", seed=1, prog=0.10, milestone="Dlvl:3"),
        _atom("s2", seed=2, prog=0.30, milestone="Dlvl:7"),
        _atom("s2", seed=3, prog=0.20, milestone="Dlvl:5"),
    ])
    _backdate(store, {1: "2026-08-23", 2: "2026-08-24", 3: "2026-08-24"})
    out = read_progress(store)
    assert out["series"] == [
        {"t": "2026-08-23", "frontier": 0.10, "ascensions": 0, "coverage": 1},
        {"t": "2026-08-24", "frontier": 0.25, "ascensions": 0, "coverage": 3},
    ]  # frontier = best program's mean; coverage = cumulative distinct cells


def test_progress_frontier_never_drops(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    _seed_objective(store)
    store.upsert_solution(digest="s1", repo="github.com/a/b", commit_sha="c1",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    store.upsert_solution(digest="s2", repo="github.com/a/b", commit_sha="c2",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    # A strong program (0.40) lands first, a weaker one (0.10) later.
    store.insert_atoms([_atom("s1", seed=1, prog=0.40),
                        _atom("s2", seed=2, prog=0.10)])
    _backdate(store, {1: "2026-08-23", 2: "2026-08-24"})
    out = read_progress(store)
    assert [p["frontier"] for p in out["series"]] == [0.40, 0.40]


def test_progress_counts_ascensions_cumulatively(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    _seed_objective(store)
    store.upsert_solution(digest="s1", repo="github.com/a/b", commit_sha="c1",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    store.insert_atoms([_atom("s1", seed=1, prog=0.90, asc=True),
                        _atom("s1", seed=2, prog=0.10, asc=False)])
    _backdate(store, {1: "2026-08-23", 2: "2026-08-24"})
    out = read_progress(store)
    assert [p["ascensions"] for p in out["series"]] == [1, 1]


def test_progress_filters_by_objective_identity(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    _seed_objective(store)
    store.upsert_solution(digest="s1", repo="github.com/a/b", commit_sha="c1",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    store.insert_atoms([_atom("s1", seed=1, prog=0.20)])
    _backdate(store, {1: "2026-08-23"})
    assert read_progress(store, objective=IDENT)["series"]           # non-empty
    assert read_progress(store, objective="wiz-elf-cha-mal") == {"series": []}


def test_progress_ignores_none_milestone_in_coverage(tmp_path):
    # An episode with no milestone (e.g. a bot_error) still counts toward the
    # program's mean, but contributes no coverage cell.
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    _seed_objective(store)
    store.upsert_solution(digest="s1", repo="github.com/a/b", commit_sha="c1",
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    store.insert_atoms([_atom("s1", seed=1, prog=0.10, milestone="Dlvl:5"),
                        _atom("s1", seed=2, prog=0.20, milestone=None)])
    _backdate(store, {1: "2026-08-23", 2: "2026-08-23"})
    out = read_progress(store)
    assert out["series"] == [
        {"t": "2026-08-23", "frontier": 0.15, "ascensions": 0, "coverage": 1},
    ]


def test_progress_empty(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    assert read_progress(store) == {"series": []}
