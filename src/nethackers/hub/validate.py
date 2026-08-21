"""Hub register write-path: the link-only registration ladder that records a
public ``repo@commit`` solution reference in the store -- no code execution,
no content hashing, no NLE, no Docker, no real network anywhere in this
module.

``register()`` runs a short, clone-free ladder, short-circuiting on the first
failure with a specific ``RegisterError`` subclass. Nothing is written to the
store until every check has passed, so a rejection always stores NOTHING:

1. identity  -- the token resolves (via ``auth.resolve``) to a login that owns
   ``reference.repo`` (its owner segment equals the login).
2. reference -- ``reference.commit`` is a full 40-hex sha the repo actually
   has, confirmed via the injected ``CommitChecker`` (a GitHub read-client in
   production, a fake in tests).

The pinned commit sha is the solution's identity: the stored ``solution_id``
is ``f"{repo}@{commit}"`` and no content digest is ever computed or verified.
``git`` is a required, injected ``CommitChecker`` -- there is no default and
no subprocess or network call anywhere in this module. ``auth.resolve``'s
``AuthError`` on a bad token is left to propagate unchanged (the API maps it
to 401); it is not this module's concern.

Scoring, leaderboards, atoms, lineage, and the derived views are deferred to
Milestone 2's held-out verifier, so ``register`` only records the link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from nethackers.hub.auth import AuthProvider, owns_repo
from nethackers.hub.store import Store

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class SolutionReference:
    """The repo+commit a registration claims to publish its solution from."""

    repo: str  # e.g. "github.com/sam/nethacker"
    commit: str  # 40-char hex sha


@dataclass(frozen=True)
class RegisterResult:
    """What a successful ``register()`` call reports back to the caller."""

    solution_id: str  # f"{repo}@{commit}"
    owner: str
    repo: str
    commit: str


class CommitChecker(Protocol):
    """The single git operation ``register`` needs -- always injected, never
    defaulted, so this module itself never shells out or hits the network."""

    def commit_exists(self, repo: str, sha: str) -> bool: ...


class RegisterError(Exception):
    """Base for every register-ladder rejection."""


class WrongOwner(RegisterError):
    """The resolved login doesn't own ``reference.repo`` (or the repo string
    doesn't even have an owner segment)."""


class MissingCommit(RegisterError):
    """``reference.commit`` isn't a 40-hex sha, or the checker doesn't have
    it."""


def register(
    store: Store,
    auth: AuthProvider,
    *,
    token: str,
    reference: SolutionReference,
    git: CommitChecker,
    root: str = "",
    now: str,
) -> RegisterResult:
    """Run the link-only ladder and, only on full success, store the
    ``repo@commit`` reference.

    Each rejection raises its own ``RegisterError`` subclass before anything
    is written. ``auth.resolve``'s ``AuthError`` on a bad token is left to
    propagate unchanged. Re-registering the same link is a safe no-op:
    ``upsert_solution`` is idempotent on the ``solution_id`` primary key.
    """
    # Step 1: identity. Guard the repo format before owns_repo, which
    # IndexErrors on a repo string with fewer than 2 "/"-segments.
    login = auth.resolve(token)
    segments = reference.repo.rstrip("/").split("/")
    if len(segments) < 2 or not owns_repo(login, reference.repo):
        raise WrongOwner(f"{login!r} does not own {reference.repo!r}")

    # Step 2: the reference is a full 40-hex sha the repo actually has.
    valid_sha = _COMMIT_RE.fullmatch(reference.commit) is not None
    if not valid_sha or not git.commit_exists(reference.repo, reference.commit):
        raise MissingCommit(f"{reference.repo}@{reference.commit} not found")

    # Store the link. The pinned sha is the identity; no content is hashed.
    solution_id = f"{reference.repo}@{reference.commit}"
    store.upsert_solution(
        solution_id,
        repo=reference.repo,
        commit_sha=reference.commit,
        owner=login,
        root=root,
        entrypoint="",
        registered_at=now,
    )
    return RegisterResult(
        solution_id=solution_id,
        owner=login,
        repo=reference.repo,
        commit=reference.commit,
    )
