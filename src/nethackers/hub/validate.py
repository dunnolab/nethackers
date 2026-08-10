"""Hub register write-path (M2a Task 11): the §6 validation ladder that
turns a submission into stored atoms + recomputed views -- no code
execution anywhere in this module (no NLE, no Docker, no real network). See
task-11-context.md, which governs this implementation.

``register()`` runs six ordered checks, short-circuiting on the first
failure with a specific ``RegisterError`` subclass. Steps 1-5 are pure
validation -- nothing is written to the store until every one of them has
passed, so a rejection always stores NOTHING:

1. identity  -- the token resolves to a login that owns ``reference.repo``.
2. reference -- ``reference.commit`` is a real 40-hex sha the repo has.
3. manifest  -- ``nethackers.solution.json`` (fetched via ``git``) is
   well-formed (``root``/``entrypoint`` present, ``parents``/``influences``
   well-typed if present).
4. digest    -- the subtree at ``(commit, manifest.root)`` recomputes to
   ``evidence.solution_digest``.
5. evidence  -- the objective is a real, published catalog entry; the
   submitted ``(seed, character)`` set is exactly that objective's
   published batch; every result's progress is finite; an evaluator image
   is present; the tier is ``"self-reported"`` (M2a's only tier).
6. store     -- upsert the objective + solution + lineage, insert atoms,
   and recompute the attainment/elite-pool views.

``git`` is a required, injected ``GitProvider`` -- there is no default and
no subprocess call anywhere in this module. Real subprocess-git
verification is **M2b-parked** (spec §10: M2a's trust model is
determinism + labeling, not cryptographic verification); ``LocalStubGit``
below is the offline trust stub tests and the local API use instead.

Rate-limiting and step-6's cross-call atomicity (each store/view call
commits its own transaction; a crash partway through step 6 could in
principle leave, say, a solution row without its atoms) are both
**parked** -- anti-gaming isn't M2a's binding constraint, and step 6 only
ever runs after every rejection check above has already passed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from nethackers.contracts.models import Evidence
from nethackers.hub.atoms import evidence_to_atoms
from nethackers.hub.auth import AuthProvider, owns_repo
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment
from nethackers.hub.views.elites import recompute_elites

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class SolutionReference:
    """The repo+commit a registration claims to publish its solution from."""

    repo: str  # e.g. "github.com/sam/nethacker"
    commit: str  # 40-char hex sha


@dataclass(frozen=True)
class RegisterResult:
    """What a successful ``register()`` call reports back to the caller."""

    solution_digest: str
    owner: str
    objective: str  # objective name (evidence.objective.seed_set)
    atoms_inserted: int


class GitProvider(Protocol):
    """The git operations ``register`` needs -- always injected, never
    defaulted, so this module itself never shells out or hits the network.
    The real subprocess-git provider is M2b-parked (module docstring)."""

    def commit_exists(self, repo: str, commit: str) -> bool: ...

    def fetch_manifest(self, repo: str, commit: str) -> dict[str, Any] | None:
        """The parsed ``nethackers.solution.json`` at ``commit``, or
        ``None`` if it's missing/unreadable."""

    def content_digest(self, repo: str, commit: str, root: str) -> str:
        """Recompute the content digest of the subtree rooted at ``root``
        in ``commit`` (a shallow fetch + rehash in the real provider)."""


@dataclass
class LocalStubGit:
    """Offline, canned ``GitProvider`` for tests and local/dev use: every
    method just echoes back a constructor argument -- no filesystem or
    network access at all. This is the trust stub M2a runs on (module
    docstring); it is not a stand-in for real verification."""

    manifest: dict[str, Any]
    digest: str
    exists: bool = True

    def commit_exists(self, repo: str, commit: str) -> bool:
        return self.exists

    def fetch_manifest(self, repo: str, commit: str) -> dict[str, Any] | None:
        return self.manifest

    def content_digest(self, repo: str, commit: str, root: str) -> str:
        return self.digest


class RegisterError(Exception):
    """Base for every register-ladder rejection."""


class WrongOwner(RegisterError):
    """Step 1: the resolved login doesn't own ``reference.repo`` (or the
    repo string doesn't even have an owner segment)."""


class MissingCommit(RegisterError):
    """Step 2: ``reference.commit`` isn't a 40-hex sha, or the provider
    doesn't have it."""


class BadManifest(RegisterError):
    """Step 3: ``nethackers.solution.json`` is missing or malformed."""


class DigestMismatch(RegisterError):
    """Step 4: the recomputed subtree digest doesn't match the evidence's
    claimed ``solution_digest``."""


class UnknownObjective(RegisterError):
    """Step 5: ``evidence.objective.seed_set`` isn't a published catalog
    objective."""


class WrongBatch(RegisterError):
    """Step 5: the submitted ``(seed, character)`` set isn't exactly the
    objective's published batch."""


class NonFiniteMetrics(RegisterError):
    """Step 5: some result's ``progress`` is NaN or infinite."""


class MissingImage(RegisterError):
    """Step 5: ``evidence.evaluator_image`` is empty."""


class WrongTier(RegisterError):
    """Step 5: ``evidence.tier`` isn't ``"self-reported"`` (M2a's only
    supported tier)."""


def register(
    store: Store,
    auth: AuthProvider,
    *,
    token: str,
    reference: SolutionReference,
    evidence: Evidence,
    git: GitProvider,
    now: str,
) -> RegisterResult:
    """Run the §6 validation ladder and, only on full success, store the
    solution + atoms and recompute the derived views.

    Each rejection raises its own ``RegisterError`` subclass before
    anything is written. ``auth.resolve``'s ``AuthError`` on a bad token is
    left to propagate unchanged -- not this module's concern (the API maps
    it to 401). Re-registering identical evidence is a safe no-op:
    ``insert_atoms`` dedups (0 newly inserted) and every step-6 write is
    independently idempotent.
    """
    # Step 1: identity. Guard the repo format before owns_repo, which
    # IndexErrors on a repo string with fewer than 2 "/"-segments.
    login = auth.resolve(token)
    segments = reference.repo.rstrip("/").split("/")
    if len(segments) < 2 or not owns_repo(login, reference.repo):
        raise WrongOwner(f"{login!r} does not own {reference.repo!r}")

    # Step 2: the reference exists.
    valid_sha = _COMMIT_RE.fullmatch(reference.commit) is not None
    if not valid_sha or not git.commit_exists(reference.repo, reference.commit):
        raise MissingCommit(f"{reference.repo}@{reference.commit} not found")

    # Step 3: manifest well-formed.
    manifest = _validated_manifest(git.fetch_manifest(reference.repo, reference.commit))

    # Step 4: digest matches.
    recomputed = git.content_digest(reference.repo, reference.commit, manifest["root"])
    if recomputed != evidence.solution_digest:
        raise DigestMismatch(f"{recomputed!r} != {evidence.solution_digest!r}")

    # Step 5: evidence well-formed.
    name = evidence.objective.seed_set
    spec = CATALOG.get(name)
    if spec is None:
        raise UnknownObjective(f"{name!r} is not a published catalog objective")

    submitted = {(r.trajectory_id, r.character) for r in evidence.results}
    if submitted != set(spec.batch):
        raise WrongBatch(f"submitted batch does not match {name!r}'s published batch")

    if not all(math.isfinite(r.progress) for r in evidence.results):
        raise NonFiniteMetrics("some result's progress is not finite")

    if not evidence.evaluator_image:
        raise MissingImage("evaluator_image is required")

    if evidence.tier != "self-reported":
        raise WrongTier(f"tier {evidence.tier!r} is not self-reported")

    # Step 6: store & recompute -- only reached once every check above has
    # passed. Individually-atomic store/view calls; cross-call atomicity
    # across this whole sequence is parked (module docstring).
    store.objectives_upsert(spec)
    store.upsert_solution(
        evidence.solution_digest,
        repo=reference.repo,
        commit_sha=reference.commit,
        owner=login,
        root=manifest["root"],
        entrypoint=manifest["entrypoint"],
        registered_at=now,
    )

    for parent in manifest.get("parents", []):
        store.add_lineage(evidence.solution_digest, parent, "parent")
    for influence in manifest.get("influences", []):
        store.add_lineage(evidence.solution_digest, influence, "influence")

    atoms = evidence_to_atoms(evidence, owner=login, spec=spec)
    inserted = store.insert_atoms(atoms)

    update_attainment(store, atoms, now=now)
    recompute_elites(store)

    return RegisterResult(
        solution_digest=evidence.solution_digest,
        owner=login,
        objective=name,
        atoms_inserted=inserted,
    )


def _validated_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Step 3: raise ``BadManifest`` unless ``manifest`` is a dict with a
    non-empty string ``root`` and ``entrypoint``, and ``parents``/
    ``influences`` -- if present at all -- each a list of strings (absent
    or empty is fine). Returns ``manifest`` unchanged so callers get a
    non-``None``, type-narrowed dict back."""
    if not isinstance(manifest, dict):
        raise BadManifest("manifest missing or not an object")
    if not isinstance(manifest.get("root"), str) or not manifest["root"]:
        raise BadManifest("manifest.root must be a non-empty string")
    if not isinstance(manifest.get("entrypoint"), str) or not manifest["entrypoint"]:
        raise BadManifest("manifest.entrypoint must be a non-empty string")
    for key in ("parents", "influences"):
        if key not in manifest:
            continue
        value = manifest[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BadManifest(f"manifest.{key} must be a list of strings")
    return manifest
