import pytest

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.store import Store
from nethackers.hub.validate import SolutionReference
from nethackers.hub.verify import (
    BadBatch,
    ParityMismatch,
    StaleSecret,
    UnknownSolution,
    register_verified,
)

SECRET = "hidden-key"
SEEDS = (4839201, 1029384)
REPO = "github.com/sam/nethacker"
SHA = "a" * 40
SID = f"{REPO}@{SHA}"


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    s.upsert_solution(SID, repo=REPO, commit_sha=SHA, owner="sam", root="bot",
                      entrypoint="bot.py", registered_at="t")
    return s


def _evidence(identity="val-dwa-law-fem", seeds=SEEDS, image=ARENA_IMAGE):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=identity, milestone=None)
        for sd in seeds)
    return Evidence.from_results(
        solution_digest=SID, objective=Objective(character=None, seed_set="v"),
        evaluator_image=image, results=results, created_at="t")


def test_register_verified_writes_verified_atoms(tmp_path):
    s = _store(tmp_path)
    res = register_verified(s, reference=SolutionReference(REPO, SHA), evidence=_evidence(),
                            secret_fingerprint=secret_fingerprint(SECRET),
                            verifier_token_fingerprint="tok", now="t",
                            expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)
    assert res.owner == "sam" and res.inserted == 2
    atoms = s.iter_verified_atoms(solution_digest=SID)
    assert len(atoms) == 2 and all(a.tier == "verified" and a.owner == "sam" for a in atoms)
    assert s.iter_atoms() == []  # self-reported table untouched


def test_register_verified_is_idempotent(tmp_path):
    s = _store(tmp_path)
    kwargs = dict(
        reference=SolutionReference(REPO, SHA), evidence=_evidence(),
        secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
        now="t", expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)
    first = register_verified(s, **kwargs)
    second = register_verified(s, **kwargs)
    assert first.inserted == 2
    assert second.inserted == 0
    assert len(s.iter_verified_atoms(solution_digest=SID)) == 2


def test_parity_mismatch_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ParityMismatch):
        register_verified(
            s, reference=SolutionReference(REPO, SHA),
            evidence=_evidence(image="wrong@sha256:0"),
            secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
            now="t", expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)


def test_stale_secret_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(StaleSecret):
        register_verified(s, reference=SolutionReference(REPO, SHA), evidence=_evidence(),
                          secret_fingerprint="deadbeef", verifier_token_fingerprint="tok",
                          now="t", expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)


def test_unknown_solution_rejected(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()  # no upsert_solution
    with pytest.raises(UnknownSolution):
        register_verified(
            s, reference=SolutionReference(REPO, SHA), evidence=_evidence(),
            secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
            now="t", expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)


def test_seed_outside_config_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(BadBatch):
        register_verified(
            s, reference=SolutionReference(REPO, SHA), evidence=_evidence(seeds=(999,)),
            secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
            now="t", expected_image=ARENA_IMAGE, hub_secret=SECRET, seeds=SEEDS)
