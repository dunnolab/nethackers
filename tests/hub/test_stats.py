"""Tests for ``nethackers.hub.views.stats``: headline counters for the
website sidebar -- straight aggregate reads over the ``solutions`` and
``atoms`` tables (program/hacker counts, ascensions, identities touched,
best progression)."""

from __future__ import annotations

from nethackers.contracts.models import Atom
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.views.stats import read_stats


def _atom(**kw):
    # objective_digest is wired to a real CATALOG entry (keyed by identity)
    # rather than the brief's literal "o1": atoms.objective_digest has a
    # FOREIGN KEY REFERENCES objectives(objective_digest), so it must name a
    # row insert_atoms can actually see -- and varying it per identity (as
    # real evidence would) keeps each atom's UNIQUE(solution_digest,
    # objective_digest, seed) key distinct from the others below.
    identity = kw.get("identity", "val-dwa-law-fem")
    base = dict(solution_digest="s1", objective_digest=CATALOG[identity].digest(), owner="alice",
                tier="self-reported", identity=identity, seed=1, progression=0.2,
                milestone="Dlvl:5", ascended=False, status="completed", turns=10, steps=10,
                evaluator_image="img")
    base.update(kw)
    return Atom(**base)


def test_read_stats_counts(tmp_path):
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    store.upsert_solution(digest="s1", repo="github.com/alice/nethacker", commit_sha="c1",
                          owner="alice", root="autoascend", entrypoint="bot.py", registered_at="t")
    store.upsert_solution(digest="s2", repo="github.com/bob/nethacker", commit_sha="c2",
                          owner="bob", root="autoascend", entrypoint="bot.py", registered_at="t")
    for identity in ("val-dwa-law-fem", "wiz-elf-cha-mal"):
        store.objectives_upsert(CATALOG[identity])
    store.insert_atoms([_atom(solution_digest="s1", owner="alice", progression=0.3),
                        _atom(solution_digest="s2", owner="bob", progression=0.6, ascended=True),
                        _atom(solution_digest="s2", owner="bob", identity="wiz-elf-cha-mal",
                              progression=0.1)])
    s = read_stats(store)
    assert s == {"programs": 2, "hackers": 2, "ascensions": 1, "identities_touched": 2, "best": 0.6}
