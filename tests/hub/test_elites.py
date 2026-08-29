"""Tests for ``nethackers.hub.views.elites``: the LIVE top-k-per-identity
window over ``atoms`` -- computed fresh on every read, no materialized
table (Part 2 of the hub API redesign dropped ``elite_pool`` +
``recompute_elites`` outright: a write to ``atoms`` is immediately visible
on the next read, with no separate recompute step to run or forget).

Ranking, per identity: mean progression (``AVG``) across that identity's
atoms at the given ``tier`` desc, ties broken by total ascensions desc,
then earliest atom first -- the exact ranking the old ``recompute_elites``
used, now expressed as one windowed SQL query.

``read_elites`` resolves ``scope`` via ``views.boards.resolve_scope``
(``"generalist"`` = all 73, a role/facet/identity narrows further), then
returns each identity's top-``k`` rows *rank-major* (``ORDER BY rank,
identity``: every identity's rank-1 first, identity-sorted, then every
rank-2, ...) -- never a single global top-k, so one identity can never
crowd out every other.
"""

from __future__ import annotations

import pytest

from nethackers.contracts.models import Atom
from nethackers.hub.ids import program_id
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.elites import read_elites

IDENTITY = "val-dwa-law-fem"
OTHER_IDENTITY = "wiz-elf-cha-fem"
# A third real identity, distinct from both of the above -- derived rather
# than hand-picked so it's guaranteed valid without re-deriving role/race/
# align legality by hand.
EXCLUDED_IDENTITY = next(i for i in IDENTITIES if i not in (IDENTITY, OTHER_IDENTITY))


def _atom(**overrides):
    identity = overrides.get("identity", IDENTITY)
    fields = dict(
        solution_digest="sha256:solution-a",
        owner="sam",
        tier="self-reported",
        identity=identity,
        seed=0,
        progression=0.5,
        milestone=None,
        ascended=False,
        status="completed",
        turns=5,
        steps=10,
        evaluator_image="img@sha256:x",
    )
    fields.update(overrides)
    return Atom(**fields)


def _new_store(tmp_path):
    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    return store


def _seed(
    store: Store, atoms: list[Atom], *, owner: str = "sam", repo: str = "r",
    commit_sha: str = "c",
) -> None:
    """One ``solutions`` row per distinct solution (``insert_atoms``' FK),
    then the atoms themselves. Safe to call more than once per test: both
    ``upsert_solution`` and ``insert_atoms`` are idempotent.

    ``owner``/``repo``/``commit_sha`` describe the *registered solution* row
    (default ``sam``/``r``/``c``) -- distinct from an atom's own ``owner``
    field."""
    for digest in {atom.solution_digest for atom in atoms}:
        store.upsert_solution(
            digest,
            repo=repo,
            commit_sha=commit_sha,
            owner=owner,
            root=".",
            entrypoint="bot.py",
            registered_at="2026-01-01T00:00:00Z",
        )
    store.insert_atoms(atoms)


def test_top_k_by_mean_progression_orders_desc_and_excludes_the_rest(tmp_path):
    # Property 1: 3 solutions on ONE identity, distinct mean progressions
    # (each averaged over 2 seeds, exercising AVG(progression) for real).
    # k=2 keeps only the top 2, ranked 1,2 by mean desc -- the 3rd (worst)
    # solution is absent. k=2 (not 1) also asserts there's no top-1
    # collapse: the result actually holds more than one entry.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:best", seed=0, progression=1.0),
        _atom(solution_digest="sha256:best", seed=1, progression=0.8),  # mean 0.9
        _atom(solution_digest="sha256:middle", seed=0, progression=0.6),
        _atom(solution_digest="sha256:middle", seed=1, progression=0.6),  # mean 0.6
        _atom(solution_digest="sha256:worst", seed=0, progression=0.3),
        _atom(solution_digest="sha256:worst", seed=1, progression=0.1),  # mean 0.2
    ]
    _seed(store, atoms)

    entries = read_elites(store, scope=IDENTITY, k=2)

    assert [e["program_id"] for e in entries] == [
        program_id("sha256:best"), program_id("sha256:middle"),
    ]
    assert "solution_digest" not in entries[0]
    assert [e["rank"] for e in entries] == [1, 2]
    assert entries[0]["score"] == pytest.approx(0.9)
    assert entries[1]["score"] == pytest.approx(0.6)
    assert all(e["identity"] == IDENTITY for e in entries)


def test_read_elites_is_a_live_view_no_recompute_step(tmp_path):
    # Property 2 (the core Part-2 change): a write to atoms is visible on
    # the VERY NEXT read_elites call -- there is no recompute_elites to run
    # (it no longer exists), and no elite_pool row to go stale. Seed a first
    # solution, read it as rank 1; seed a second, better solution (no
    # intervening "recompute" call of any kind); the read immediately
    # reflects it.
    store = _new_store(tmp_path)
    _seed(store, [_atom(solution_digest="sha256:first", seed=0, progression=0.4)])

    before = read_elites(store, scope=IDENTITY, k=2)
    assert [e["program_id"] for e in before] == [program_id("sha256:first")]

    _seed(store, [_atom(solution_digest="sha256:better", seed=0, progression=0.8)])
    after = read_elites(store, scope=IDENTITY, k=2)

    assert [e["program_id"] for e in after] == [
        program_id("sha256:better"), program_id("sha256:first"),
    ]


def test_equal_mean_progression_ties_broken_by_more_ascensions(tmp_path):
    # Property 3: two solutions with equal mean progression -- the one with
    # more ascensions ranks higher.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:ascended-once", seed=0, progression=0.5, ascended=True),
        _atom(solution_digest="sha256:never-ascended", seed=0, progression=0.5, ascended=False),
    ]
    _seed(store, atoms)

    entries = read_elites(store, scope=IDENTITY, k=2)

    assert [e["program_id"] for e in entries] == [
        program_id("sha256:ascended-once"), program_id("sha256:never-ascended"),
    ]
    assert [e["rank"] for e in entries] == [1, 2]


def test_generalist_scope_spreads_rank_major_across_identities_not_identity_major(tmp_path):
    # Property 4: 2 identities, 2 elites each. read_elites(scope="generalist")
    # must come back rank-major (every identity's rank-1, then every
    # rank-2) -- NOT identity-major (all of one identity's top-k before the
    # next). IDENTITY ("val-...") sorts before OTHER_IDENTITY ("wiz-...").
    store = _new_store(tmp_path)
    atoms = [
        _atom(identity=IDENTITY, solution_digest="sha256:a1", seed=0, progression=0.9),
        _atom(identity=IDENTITY, solution_digest="sha256:a2", seed=0, progression=0.5),
        _atom(identity=OTHER_IDENTITY, solution_digest="sha256:b1", seed=0, progression=0.9),
        _atom(identity=OTHER_IDENTITY, solution_digest="sha256:b2", seed=0, progression=0.5),
    ]
    _seed(store, atoms)

    entries = read_elites(store, scope="generalist", k=2)
    rank_major = [(e["identity"], e["rank"]) for e in entries]

    assert rank_major == [
        (IDENTITY, 1),
        (OTHER_IDENTITY, 1),
        (IDENTITY, 2),
        (OTHER_IDENTITY, 2),
    ]
    identity_major = [(IDENTITY, 1), (IDENTITY, 2), (OTHER_IDENTITY, 1), (OTHER_IDENTITY, 2)]
    assert rank_major != identity_major


def test_role_scope_excludes_identities_outside_the_role(tmp_path):
    # Property 5: a role scope's rollup covers only that role's identities --
    # an identity with atoms but NOT in the role must be excluded from the
    # result. (Pre-redesign this was "a random-kind objective's rollup spans
    # only its own sampled identities" -- the new scope vocabulary is
    # generalist/role/facet/identity, so this proves the same "out-of-scope
    # identity is excluded" property via a real role token.)
    store = _new_store(tmp_path)
    atoms = [
        _atom(identity=IDENTITY, solution_digest="sha256:a1", seed=0, progression=0.9),
        _atom(identity=EXCLUDED_IDENTITY, solution_digest="sha256:c1", seed=0, progression=0.9),
    ]
    _seed(store, atoms)

    role = IDENTITY.split("-")[0]  # "val"
    entries = read_elites(store, scope=role, k=2)

    identities = {e["identity"] for e in entries}
    assert IDENTITY in identities
    assert EXCLUDED_IDENTITY not in identities


def test_unknown_scope_raises_value_error(tmp_path):
    # read_elites resolves scope via resolve_scope -- an unrecognized token
    # raises ValueError (mapping that to a 404 is the caller's job, same as
    # /board's scope resolution).
    store = _new_store(tmp_path)
    with pytest.raises(ValueError):
        read_elites(store, scope="not-a-real-scope")


def test_read_elites_empty_when_no_atoms(tmp_path):
    store = _new_store(tmp_path)
    assert read_elites(store, scope=IDENTITY) == []


def test_entries_carry_owner_for_trust_aware_select(tmp_path):
    # Each entry is enriched with the registered solution's owner AND
    # reference (both via the same LEFT JOIN solutions ON solution_digest =
    # solutions.digest), and program_id replaces the old solution_digest.
    # NOTE: repo/commit_sha are no longer bare row fields (they're nested
    # under reference) and tier is gone entirely -- Part 2's row shape is
    # {rank, identity, program_id, owner, score}; Task 2b adds reference
    # back (repo/commit only, nested) specifically for harness/select.py's
    # trust-aware SELECT (per_identity_elites), which resolves the elite's
    # tree on disk and needs a git pointer.
    store = _new_store(tmp_path)
    atoms = [_atom(solution_digest="sha256:solution-a", seed=0, progression=0.5)]
    _seed(store, atoms, owner="dev", repo="github.com/dev/nethacker-runs", commit_sha="d" * 40)

    entries = read_elites(store, scope=IDENTITY, k=2)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["owner"] == "dev"
    assert entry["identity"] == IDENTITY
    assert entry["program_id"] == program_id("sha256:solution-a")
    assert entry["rank"] == 1
    assert entry["reference"] == {"repo": "github.com/dev/nethacker-runs", "commit": "d" * 40}
    assert set(entry) == {"rank", "identity", "program_id", "owner", "score", "reference"}
