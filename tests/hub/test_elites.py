"""Tests for ``nethackers.hub.views.elites``: the displaceable, full-recompute
top-k current-best-per-identity view (M2a Task 8). See task-8-context.md --
unlike attainment's monotonic append-only record (Task 7), ``elite_pool`` is
wholesale DELETEd and rebuilt on every ``recompute_elites`` call: a newly-
better solution simply appears in the rebuilt top-k, and whatever now falls
outside it is simply not reinserted (no explicit "evict" step needed).

Ranking, per identity: mean progression (``AVG``) across that identity's
atoms desc, ties broken by total ascensions desc, then earliest atom first.
``read_elites`` rolls a functional/random objective up into a *spread*
across identities -- round-robin (``ORDER BY rank, identity``: every
identity's rank-1 first, identity-sorted, then every rank-2, ...) -- never a
single global top-k, so one identity can never crowd out every other.
"""

from __future__ import annotations

import pytest

from nethackers.contracts.models import Atom, ObjectiveSpec
from nethackers.hub.objectives import CATALOG, IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.elites import read_elites, recompute_elites

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
        objective_digest=CATALOG[identity].digest(),
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
    """Seed every FK parent ``insert_atoms`` needs -- ``atoms`` has FKs to
    both ``objectives`` and ``solutions`` (task-8-context.md's test-setup
    note) -- then insert the atoms themselves. Safe to call more than once
    per test: both upserts and ``insert_atoms`` are idempotent.

    ``owner``/``repo``/``commit_sha`` describe the *registered solution*
    row (default ``sam``/``r``/``c``, matching every pre-existing caller)
    -- distinct from an atom's own ``owner`` field, and the knob the
    owner/repo/commit_sha-enrichment tests override."""
    for identity in {atom.identity for atom in atoms}:
        store.objectives_upsert(CATALOG[identity])
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
    # recompute_elites(k=2) keeps only the top 2, ranked 1,2 by mean desc --
    # the 3rd (worst) solution is absent. k=2 (not 1) also asserts there's
    # no top-1 collapse: the pool actually holds more than one entry.
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

    recompute_elites(store, k=2)
    entries = read_elites(store, objective=IDENTITY)

    assert [e["solution_digest"] for e in entries] == ["sha256:best", "sha256:middle"]
    assert [e["rank"] for e in entries] == [1, 2]
    assert entries[0]["score"] == pytest.approx(0.9)
    assert entries[1]["score"] == pytest.approx(0.6)
    assert all(e["identity"] == IDENTITY for e in entries)


def test_recompute_is_displaceable_a_newly_better_solution_evicts_the_worst(tmp_path):
    # Property 2: an initial top-2 [best, middle]; a new solution beating
    # middle (but not best) enters on the next recompute, and middle --
    # the one it displaced -- drops out of the pool entirely.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:best", seed=0, progression=0.9),
        _atom(solution_digest="sha256:middle", seed=0, progression=0.6),
        _atom(solution_digest="sha256:worst", seed=0, progression=0.2),
    ]
    _seed(store, atoms)
    recompute_elites(store, k=2)
    before = [e["solution_digest"] for e in read_elites(store, objective=IDENTITY)]
    assert before == ["sha256:best", "sha256:middle"]

    _seed(store, [_atom(solution_digest="sha256:newcomer", seed=0, progression=0.7)])
    recompute_elites(store, k=2)

    after = read_elites(store, objective=IDENTITY)
    assert [e["solution_digest"] for e in after] == ["sha256:best", "sha256:newcomer"]
    assert "sha256:middle" not in [e["solution_digest"] for e in after]


def test_equal_mean_progression_ties_broken_by_more_ascensions(tmp_path):
    # Property 3: two solutions with equal mean progression -- the one with
    # more ascensions ranks higher.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:ascended-once", seed=0, progression=0.5, ascended=True),
        _atom(solution_digest="sha256:never-ascended", seed=0, progression=0.5, ascended=False),
    ]
    _seed(store, atoms)

    recompute_elites(store, k=2)
    entries = read_elites(store, objective=IDENTITY)

    assert [e["solution_digest"] for e in entries] == [
        "sha256:ascended-once",
        "sha256:never-ascended",
    ]
    assert [e["rank"] for e in entries] == [1, 2]


def test_all_rollup_spreads_rank_major_across_identities_not_identity_major(tmp_path):
    # Property 4: 2 identities, 2 elites each. read_elites(objective="all")
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
    recompute_elites(store, k=2)

    entries = read_elites(store, objective="all")
    rank_major = [(e["identity"], e["rank"]) for e in entries]

    assert rank_major == [
        (IDENTITY, 1),
        (OTHER_IDENTITY, 1),
        (IDENTITY, 2),
        (OTHER_IDENTITY, 2),
    ]
    identity_major = [(IDENTITY, 1), (IDENTITY, 2), (OTHER_IDENTITY, 1), (OTHER_IDENTITY, 2)]
    assert rank_major != identity_major


def test_random_rollup_spans_only_the_samples_identities(tmp_path, monkeypatch):
    # Property 5: a random-kind objective's rollup covers only its own
    # sampled identities -- an identity with elite_pool entries but NOT in
    # the sample must be excluded from the result.
    store = _new_store(tmp_path)
    atoms = [
        _atom(identity=IDENTITY, solution_digest="sha256:a1", seed=0, progression=0.9),
        _atom(identity=OTHER_IDENTITY, solution_digest="sha256:b1", seed=0, progression=0.9),
        _atom(identity=EXCLUDED_IDENTITY, solution_digest="sha256:c1", seed=0, progression=0.9),
    ]
    _seed(store, atoms)
    recompute_elites(store, k=2)

    sample_spec = ObjectiveSpec(
        name="test-random-sample",
        kind="random",
        batch=((0, IDENTITY), (1, OTHER_IDENTITY)),
        max_steps=1000,
        no_progress_timeout=100,
        action_timeout_seconds=5.0,
        aggregation="asc_median_mean",
    )
    monkeypatch.setitem(CATALOG, "test-random-sample", sample_spec)

    entries = read_elites(store, objective="test-random-sample")

    identities = {e["identity"] for e in entries}
    assert identities == {IDENTITY, OTHER_IDENTITY}
    assert EXCLUDED_IDENTITY not in identities


def test_recompute_elites_is_idempotent(tmp_path):
    # Property 6: recomputing twice with unchanged atoms leaves identical
    # elite_pool rows.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:a", seed=0, progression=0.9),
        _atom(solution_digest="sha256:b", seed=0, progression=0.5),
        _atom(solution_digest="sha256:c", seed=0, progression=0.2),
    ]
    _seed(store, atoms)

    recompute_elites(store, k=2)
    first = store.conn.execute(
        "SELECT identity, solution_digest, score, rank FROM elite_pool ORDER BY identity, rank"
    ).fetchall()

    recompute_elites(store, k=2)
    second = store.conn.execute(
        "SELECT identity, solution_digest, score, rank FROM elite_pool ORDER BY identity, rank"
    ).fetchall()

    assert first == second
    assert len(first) == 2


def test_entries_carry_owner_tier_repo_commit_sha_for_trust_aware_select(tmp_path):
    # Property 7 (M3 SELECT-from-hub): each entry is enriched with the
    # registered solution's {owner, repo, commit_sha} (LEFT JOIN solutions
    # ON solution_digest = solutions.digest) plus a constant tier -- every
    # atom is self-reported until M2b verification exists. The trust-aware
    # SELECT resolver (harness/select.py) needs these fields to decide
    # whether an elite is trusted.
    store = _new_store(tmp_path)
    atoms = [_atom(solution_digest="sha256:solution-a", seed=0, progression=0.5)]
    _seed(store, atoms, owner="dev", repo="github.com/dev/nethacker-runs", commit_sha="d" * 40)

    recompute_elites(store, k=2)
    entries = read_elites(store, objective=IDENTITY)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["owner"] == "dev"
    assert entry["tier"] == "self-reported"
    assert entry["repo"] == "github.com/dev/nethacker-runs"
    assert entry["commit_sha"] == "d" * 40
    # Existing fields untouched.
    assert entry["identity"] == IDENTITY
    assert entry["solution_digest"] == "sha256:solution-a"
    assert entry["rank"] == 1
