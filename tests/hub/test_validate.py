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
from nethackers.hub.objectives import CATALOG, build_union_spec
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    MissingCommit,
    SolutionReference,
    WrongBatch,
    WrongOwner,
    register,
)
from nethackers.hub.views.elites import read_elites


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


def _evidence_with_a_milestone(objective_name: str = OBJ) -> Evidence:
    """Like ``_evidence()``, but the first result carries a real milestone --
    ``_evidence()``'s results are all ``milestone=None`` (a deliberate
    bot-failure stand-in elsewhere), which ``update_attainment`` skips
    entirely, so it alone can never prove attainment gets populated."""
    from nethackers.arena.progress import ACHIEVEMENTS

    batch = CATALOG[objective_name].batch
    results = tuple(
        TrajectoryResult(
            trajectory_id=seed, status="completed",
            progress=ACHIEVEMENTS["Dlvl:2"] if i == 0 else 0.5,
            ascended=False, steps=1, turns=1, max_depth=1, end_status="died", error=None,
            wall_seconds=0.1, character=character,
            milestone="Dlvl:2" if i == 0 else None,
        )
        for i, (seed, character) in enumerate(batch)
    )
    return Evidence.from_results(
        solution_digest="sha256:" + "ab" * 32,
        objective=Objective(character=None, seed_set=objective_name),
        evaluator_image="img", results=results, created_at="t",
    )


def test_register_writes_atoms_and_attainment_but_never_recomputes_elites(tmp_path):
    # Part 2 of the hub API redesign: register no longer touches elites at
    # all -- no elite_pool table exists (it's dropped from the schema), and
    # /elites is a live query, so the atoms register just wrote are visible
    # immediately with no separate recompute step.
    s = _store(tmp_path)
    register(
        s,
        LocalStubAuth({"t": "sam"}),
        token="t",
        reference=SolutionReference("github.com/sam/nethacker", SHA),
        manifest=MANIFEST,
        evidence=_evidence_with_a_milestone(),
        git=_Git(),
        now="2026-08-29T00:00:00Z",
    )

    solution_id = "github.com/sam/nethacker@" + SHA
    assert len(s.iter_atoms(solution_digest=solution_id)) == len(CATALOG[OBJ].batch)
    from nethackers.hub.views.attainment import read_attainment

    assert read_attainment(s) != []  # attainment IS populated

    tables = [r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "elite_pool" not in tables

    rows = read_elites(s, scope=OBJ)
    assert rows and rows[0]["rank"] == 1  # elites computed live, no recompute needed

    rows = read_elites(s, scope=OBJ)
    assert rows and rows[0]["rank"] == 1  # elites computed live, no recompute needed


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


def _set_evidence(identities, name):
    spec = build_union_spec(identities, name=name)
    results = tuple(
        TrajectoryResult(
            trajectory_id=seed, status="completed", progress=0.5, ascended=False,
            steps=1, turns=1, max_depth=1, end_status="died", error=None,
            wall_seconds=0.1, character=character, milestone=None)
        for seed, character in spec.batch)
    return Evidence.from_results(
        solution_digest="sha256:" + "cd" * 32,
        objective=Objective(character=None, seed_set=name),
        evaluator_image="img", results=results, created_at="t")


def test_register_slices_a_set_objective_into_per_identity_atoms(tmp_path):
    s = _store(tmp_path)
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]
    register(
        s, LocalStubAuth({"t": "sam"}), token="t",
        reference=SolutionReference("github.com/sam/nethacker", SHA),
        manifest=MANIFEST, evidence=_set_evidence(ids, "set:2:deadbeef"),
        git=_Git(), now="2026-08-26T00:00:00Z")
    sol = "github.com/sam/nethacker@" + SHA
    # atoms keyed by identity for BOTH members, from ONE register call
    for ident in ids:
        stored = s.iter_atoms(solution_digest=sol, identity=ident)
        assert len(stored) == len(CATALOG[ident].batch)
        assert all(a.identity == ident for a in stored)


def test_register_wrong_batch_for_a_cherry_picked_set(tmp_path):
    s = _store(tmp_path)
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]
    ev = _set_evidence(ids, "set:2:deadbeef")
    ev = Evidence.from_results(                       # drop one episode -> cherry-pick
        solution_digest=ev.solution_digest, objective=ev.objective,
        evaluator_image=ev.evaluator_image, results=ev.results[:-1],
        created_at=ev.created_at)
    with pytest.raises(WrongBatch):
        register(s, LocalStubAuth({"t": "sam"}), token="t",
                 reference=SolutionReference("github.com/sam/nethacker", SHA),
                 manifest=MANIFEST, evidence=ev, git=_Git(), now="n")
