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
    identity = kw.get("identity", "val-dwa-law-fem")
    base = dict(solution_digest="s1", owner="alice",
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
    assert s == {"programs": 2, "hackers": 2, "ascensions": 1, "identities_touched": 2,
                 "best": 0.6, "last_registered_at": "t"}


def test_read_stats_last_registered_at_is_the_latest(tmp_path):
    # The sidebar "last updated" stamp reads MAX(registered_at) -- the most
    # recent registration, not row order.
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    store.upsert_solution(digest="s1", repo="github.com/alice/nethacker", commit_sha="c1",
                          owner="alice", root="autoascend", entrypoint="bot.py",
                          registered_at="2026-08-01T00:00:00+00:00")
    store.upsert_solution(digest="s2", repo="github.com/bob/nethacker", commit_sha="c2",
                          owner="bob", root="autoascend", entrypoint="bot.py",
                          registered_at="2026-08-27T09:30:00+00:00")
    assert read_stats(store)["last_registered_at"] == "2026-08-27T09:30:00+00:00"


def test_read_stats_last_registered_at_is_none_when_empty(tmp_path):
    # No registrations -> no stamp (the page shows a neutral dash, never a fake date).
    store = Store(str(tmp_path / "h.db"))
    store.init_schema()
    assert read_stats(store)["last_registered_at"] is None
