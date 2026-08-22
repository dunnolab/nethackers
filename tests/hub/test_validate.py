"""Tests for ``nethackers.hub.validate``: the atom-based register ladder
keyed by the ``repo@commit`` link.

No code execution and no network anywhere -- ``git`` is always a local
``_Git`` fake exposing ``commit_exists``, and ``auth`` is a ``LocalStubAuth``.
The ladder resolves the caller's login, checks repo ownership, checks the
commit is a full 40-hex sha the repo has, validates the manifest, then
validates the self-reported ``Evidence`` (published objective, exact batch,
finite metrics, evaluator image, self-reported tier); only then is the
``repo@commit`` link stored with its per-identity atoms. Each rejection
raises its own ``RegisterError`` subclass.
"""

from __future__ import annotations

import pytest

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.auth import AuthError, LocalStubAuth
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    MissingCommit,
    SolutionReference,
    WrongBatch,
    WrongOwner,
    register,
)


class _Git:
    def __init__(self, exists=True):
        self.exists = exists

    def commit_exists(self, repo, sha):
        return self.exists


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    return s


SHA = "a" * 40

# A real, published single-identity objective -> its exact 15-episode batch.
OBJ = "val-dwa-law-fem"
MANIFEST = {"root": "bot", "entrypoint": "bot.py"}


def _evidence(objective_name: str = OBJ, *, full: bool = True) -> Evidence:
    """Valid self-reported evidence whose ``(trajectory_id, character)`` set is
    exactly ``objective_name``'s published batch. ``full=False`` drops all but
    the first episode, so the submitted set no longer matches the batch."""
    batch = CATALOG[objective_name].batch
    pairs = batch if full else batch[:1]
    results = tuple(
        TrajectoryResult(
            trajectory_id=seed, status="completed", progress=0.5, ascended=False,
            steps=1, turns=1, max_depth=1, end_status="died", error=None,
            wall_seconds=0.1, character=character, milestone=None,
        )
        for seed, character in pairs
    )
    return Evidence.from_results(
        solution_digest="sha256:" + "ab" * 32,
        objective=Objective(character=None, seed_set=objective_name),
        evaluator_image="img", results=results, created_at="t",
    )


def test_register_stores_link(tmp_path):
    s = _store(tmp_path)
    solution_id = "github.com/sam/nethacker@" + SHA
    res = register(
        s,
        LocalStubAuth({"t": "sam"}),
        token="t",
        reference=SolutionReference("github.com/sam/nethacker", SHA),
        manifest=MANIFEST,
        evidence=_evidence(),
        git=_Git(),
        now="2026-08-22T00:00:00Z",
    )
    assert res.solution_id == solution_id
    assert res.owner == "sam"
    assert res.objective == OBJ
    # One atom per published episode, all inserted, all keyed to the link id.
    assert res.atoms_inserted == len(CATALOG[OBJ].batch)
    stored = s.iter_atoms(solution_digest=solution_id)
    assert len(stored) == len(CATALOG[OBJ].batch)
    assert all(a.solution_digest == solution_id for a in stored)

    row = s.get_solution(solution_id)
    assert row["owner"] == "sam" and row["commit_sha"] == SHA and row["root"] == "bot"


def test_wrong_owner(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(WrongOwner):
        register(
            s,
            LocalStubAuth({"t": "sam"}),
            token="t",
            reference=SolutionReference("github.com/eve/nethacker", SHA),
            manifest=MANIFEST,
            evidence=_evidence(),
            git=_Git(),
            now="n",
        )


def test_bad_sha(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(MissingCommit):
        register(
            s,
            LocalStubAuth({"t": "sam"}),
            token="t",
            reference=SolutionReference("github.com/sam/nethacker", "main"),
            manifest=MANIFEST,
            evidence=_evidence(),
            git=_Git(),
            now="n",
        )


def test_missing_commit(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(MissingCommit):
        register(
            s,
            LocalStubAuth({"t": "sam"}),
            token="t",
            reference=SolutionReference("github.com/sam/nethacker", SHA),
            manifest=MANIFEST,
            evidence=_evidence(),
            git=_Git(exists=False),
            now="n",
        )


def test_wrong_batch(tmp_path):
    # Evidence carrying only a subset of the objective's published batch is
    # rejected before anything is stored.
    s = _store(tmp_path)
    with pytest.raises(WrongBatch):
        register(
            s,
            LocalStubAuth({"t": "sam"}),
            token="t",
            reference=SolutionReference("github.com/sam/nethacker", SHA),
            manifest=MANIFEST,
            evidence=_evidence(full=False),
            git=_Git(),
            now="n",
        )
    assert s.get_solution("github.com/sam/nethacker@" + SHA) is None


def test_auth_error_propagates(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(AuthError):
        register(
            s,
            LocalStubAuth({}),
            token="nope",
            reference=SolutionReference("github.com/sam/nethacker", SHA),
            manifest=MANIFEST,
            evidence=_evidence(),
            git=_Git(),
            now="n",
        )
