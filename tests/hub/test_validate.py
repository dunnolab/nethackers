"""Tests for ``nethackers.hub.validate``: the link-only register ladder.

No code execution and no network anywhere -- ``git`` is always a local
``_Git`` fake exposing ``commit_exists``, and ``auth`` is a ``LocalStubAuth``.
The ladder resolves the caller's login, checks repo ownership, then checks the
commit is a full 40-hex sha the repo has; only then is the ``repo@commit``
link stored. Each rejection raises its own ``RegisterError`` subclass.
"""

from __future__ import annotations

import pytest

from nethackers.hub.auth import AuthError, LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    MissingCommit,
    SolutionReference,
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


def test_register_stores_link(tmp_path):
    s = _store(tmp_path)
    res = register(
        s,
        LocalStubAuth({"t": "sam"}),
        token="t",
        reference=SolutionReference("github.com/sam/nethacker", SHA),
        git=_Git(),
        root="bot",
        now="2026-08-22T00:00:00Z",
    )
    assert res.solution_id == "github.com/sam/nethacker@" + SHA
    assert res.owner == "sam"
    row = s.get_solution("github.com/sam/nethacker@" + SHA)
    assert row["owner"] == "sam" and row["commit_sha"] == SHA and row["root"] == "bot"


def test_wrong_owner(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(WrongOwner):
        register(
            s,
            LocalStubAuth({"t": "sam"}),
            token="t",
            reference=SolutionReference("github.com/eve/nethacker", SHA),
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
            git=_Git(exists=False),
            now="n",
        )


def test_auth_error_propagates(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(AuthError):
        register(
            s,
            LocalStubAuth({}),
            token="nope",
            reference=SolutionReference("github.com/sam/nethacker", SHA),
            git=_Git(),
            now="n",
        )
