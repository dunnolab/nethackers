"""Tests for ``nethackers.hub.validate``: the register write-path's §6
validation ladder (M2a Task 11) -- no code execution anywhere (``git`` is
always an injected ``LocalStubGit``). See task-11-context.md, which governs:
each ladder failure raises its own ``RegisterError`` subclass BEFORE any
store write, so every rejection test also asserts nothing was stored.

``SPEC``/``IDENTITY`` are a real ``CATALOG`` entry (not a hand-rolled
``ObjectiveSpec``) so the evidence built here has to satisfy step 5's
"submitted (seed, character) set == the objective's published batch" check
for real, exactly like a real registration would.
"""

from __future__ import annotations

import pytest

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    BadManifest,
    DigestMismatch,
    LocalStubGit,
    MissingCommit,
    MissingImage,
    NonFiniteMetrics,
    RegisterResult,
    SolutionReference,
    UnknownObjective,
    WrongBatch,
    WrongOwner,
    WrongTier,
    register,
)
from nethackers.hub.views.attainment import read_attainment
from nethackers.hub.views.elites import read_elites

IDENTITY = "val-dwa-law-fem"
SPEC = CATALOG[IDENTITY]  # a real, published identity objective
BATCH_SIZE = len(SPEC.batch)

TOKEN = "tok-sam"
OWNER = "sam"
AUTH = LocalStubAuth({TOKEN: OWNER})

REPO = f"github.com/{OWNER}/nethacker"
COMMIT = "a" * 40  # a well-formed (if fake) 40-hex sha
DIGEST = "sha256:solution-a"
NOW = "2026-01-01T00:00:00Z"
PARENT_DIGEST = "sha256:parent-x"

MANIFEST = {
    "root": ".",
    "entrypoint": "bot.py",
    "parents": [PARENT_DIGEST],
    "influences": [],
}


def _result(trajectory_id, character, **overrides):
    fields = dict(
        trajectory_id=trajectory_id,
        status="completed",
        progress=0.02 + 0.01 * trajectory_id,
        ascended=False,
        steps=100 + trajectory_id,
        turns=90 + trajectory_id,
        max_depth=3,
        end_status=None,
        error=None,
        wall_seconds=1.0,
        character=character,
        milestone="Dlvl:3",
    )
    fields.update(overrides)
    return TrajectoryResult(**fields)


def _happy_results() -> list[TrajectoryResult]:
    # Exactly SPEC's published (seed, character) batch, in order.
    return [_result(seed, character) for seed, character in SPEC.batch]


def _evidence(*, results, **overrides):
    fields = dict(
        solution_digest=DIGEST,
        objective=Objective(character=None, seed_set=IDENTITY),
        evaluator_image="img@sha256:evaluator",
        results=results,
        created_at=NOW,
    )
    fields.update(overrides)
    return Evidence.from_results(**fields)


def _new_store(tmp_path):
    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    return store


def _git(**overrides):
    fields = dict(manifest=MANIFEST, digest=DIGEST, exists=True)
    fields.update(overrides)
    return LocalStubGit(**fields)


def _reference(**overrides):
    fields = dict(repo=REPO, commit=COMMIT)
    fields.update(overrides)
    return SolutionReference(**fields)


def _assert_nothing_stored(store: Store) -> None:
    assert store.get_solution(DIGEST) is None
    assert store.iter_atoms() == []


def test_happy_path_stores_atoms_and_lights_views(tmp_path):
    # Property 1: valid everything -> RegisterResult with atoms_inserted ==
    # len(results); the solution, its lineage, the atoms, attainment cells,
    # and the elite pool are all populated by the single register() call.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())

    result = register(
        store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence, git=_git(), now=NOW
    )

    assert result == RegisterResult(
        solution_digest=DIGEST, owner=OWNER, objective=IDENTITY, atoms_inserted=BATCH_SIZE
    )

    stored = store.get_solution(DIGEST)
    assert stored is not None
    assert stored["owner"] == OWNER
    assert stored["repo"] == REPO
    assert stored["commit_sha"] == COMMIT
    assert stored["root"] == MANIFEST["root"]
    assert stored["entrypoint"] == MANIFEST["entrypoint"]

    assert len(store.iter_atoms(solution_digest=DIGEST)) == BATCH_SIZE

    lineage = store.conn.execute(
        "SELECT parent_digest, kind FROM lineage WHERE child_digest = ?", (DIGEST,)
    ).fetchall()
    assert (PARENT_DIGEST, "parent") in lineage

    assert read_attainment(store, identity=IDENTITY) != []
    assert read_elites(store, objective=IDENTITY) != []


def test_wrong_owner_rejects_before_any_store_write(tmp_path):
    # Property 2: the token resolves to "sam", but the repo is "other"'s ->
    # WrongOwner, nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())

    with pytest.raises(WrongOwner):
        register(
            store, AUTH, token=TOKEN,
            reference=_reference(repo="github.com/other/nethacker"),
            evidence=evidence, git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_missing_commit_rejects_before_any_store_write(tmp_path):
    # Property 3: a well-formed sha the provider doesn't have (exists=False)
    # -> MissingCommit, nothing stored. A malformed sha (too short) must
    # also raise MissingCommit -- and must do so without even asking the
    # provider (regression guard: regex check runs before commit_exists).
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())

    with pytest.raises(MissingCommit):
        register(
            store, AUTH, token=TOKEN, reference=_reference(),
            evidence=evidence, git=_git(exists=False), now=NOW,
        )
    _assert_nothing_stored(store)

    with pytest.raises(MissingCommit):
        register(
            store, AUTH, token=TOKEN, reference=_reference(commit="not-a-real-sha"),
            evidence=evidence, git=_git(), now=NOW,
        )
    _assert_nothing_stored(store)


def test_bad_manifest_rejects_before_any_store_write(tmp_path):
    # Property 4: a manifest missing `entrypoint` -> BadManifest, nothing
    # stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())
    bad_manifest = {"root": ".", "parents": [], "influences": []}

    with pytest.raises(BadManifest):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(manifest=bad_manifest), now=NOW,
        )

    _assert_nothing_stored(store)


def test_digest_mismatch_rejects_before_any_store_write(tmp_path):
    # Property 5: the recomputed content digest doesn't match
    # evidence.solution_digest -> DigestMismatch, nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())

    with pytest.raises(DigestMismatch):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(digest="sha256:wrong"), now=NOW,
        )

    _assert_nothing_stored(store)


def test_unknown_objective_rejects_before_any_store_write(tmp_path):
    # Property 6: evidence.objective.seed_set names no catalog objective ->
    # UnknownObjective, nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(
        results=_happy_results(),
        objective=Objective(character=None, seed_set="not-a-real-objective"),
    )

    with pytest.raises(UnknownObjective):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_wrong_batch_rejects_before_any_store_write(tmp_path):
    # Property 7: dropping one result means the submitted (seed, character)
    # set is a strict subset of spec.batch -> WrongBatch, nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results()[:-1])

    with pytest.raises(WrongBatch):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_non_finite_metrics_rejects_before_any_store_write(tmp_path):
    # Additional step-5 coverage beyond the brief's enumerated 9 (still a
    # named RegisterError the ladder must raise): one result's progress is
    # NaN -- the (seed, character) set is untouched, so this exercises the
    # finiteness check specifically, not WrongBatch.
    store = _new_store(tmp_path)
    seed, character = SPEC.batch[0]
    results = [_result(seed, character, progress=float("nan"))] + _happy_results()[1:]
    evidence = _evidence(results=results)

    with pytest.raises(NonFiniteMetrics):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_missing_evaluator_image_rejects_before_any_store_write(tmp_path):
    # Additional step-5 coverage beyond the brief's enumerated 9: an empty
    # evaluator_image -> MissingImage, nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results(), evaluator_image="")

    with pytest.raises(MissingImage):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_non_tier_1_rejects_before_any_store_write(tmp_path):
    # Property 8: tier="replay-verified" (not "self-reported") -> WrongTier,
    # nothing stored.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results(), tier="replay-verified")

    with pytest.raises(WrongTier):
        register(
            store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence,
            git=_git(), now=NOW,
        )

    _assert_nothing_stored(store)


def test_idempotent_reregister_inserts_zero_new_atoms(tmp_path):
    # Property 9: registering identical evidence twice -> the second call's
    # atoms_inserted == 0, one solution row, and every view unchanged.
    store = _new_store(tmp_path)
    evidence = _evidence(results=_happy_results())

    first = register(
        store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence, git=_git(), now=NOW
    )
    assert first.atoms_inserted == BATCH_SIZE

    atoms_after_first = store.iter_atoms()
    attainment_after_first = read_attainment(store, identity=IDENTITY)
    elites_after_first = read_elites(store, objective=IDENTITY)
    solutions_after_first = store.conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0]

    second = register(
        store, AUTH, token=TOKEN, reference=_reference(), evidence=evidence, git=_git(), now=NOW
    )

    assert second.atoms_inserted == 0
    assert store.iter_atoms() == atoms_after_first
    assert read_attainment(store, identity=IDENTITY) == attainment_after_first
    assert read_elites(store, objective=IDENTITY) == elites_after_first
    assert solutions_after_first == 1
    assert store.conn.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 1
