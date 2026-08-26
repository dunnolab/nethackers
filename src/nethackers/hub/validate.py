"""Hub register write-path: the validation ladder that turns a submission
(a public ``repo@commit`` link + its per-identity self-reported evidence) into
a stored solution + atoms + recomputed views.

Reconciles two models:

- the **link identity** (M1): a submission is a public ``repo@commit`` the
  caller owns; the pinned commit SHA *is* the solution identity
  (``repo@commit``), so it is fetchable and no content digest is computed or
  verified here.
- the **atom model** (v1 / generalist objectives): the submission carries
  ``Evidence`` (a batch of per-identity results); those become per-identity
  atoms at ``tier="self-reported"``, which the boards / attainment / elite
  views aggregate. A generalist win registers one slice per identity (see
  ``harness.register.register_win_slices``), so one solution row grows atoms
  across the whole identity set.

The held-out re-evaluation (a later "verified" tier) is out of scope: here the
reporter's own eval is trusted (self-reported), while provenance is the *real*
commit-exists check, so the registered link is genuinely fetchable.

``git`` is an injected ``CommitChecker`` (the real ``GitHubRead`` in the API,
a fake in tests) -- register never clones or hashes; it only asks whether the
commit exists.
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
    """The repo + commit a registration publishes its solution from."""

    repo: str  # e.g. "github.com/sam/nethacker"
    commit: str  # 40-char hex sha


@dataclass(frozen=True)
class RegisterResult:
    """What a successful ``register()`` reports back."""

    solution_id: str  # "<repo>@<commit>"
    owner: str
    objective: str  # the objective name (evidence.objective.seed_set)
    atoms_inserted: int


class CommitChecker(Protocol):
    """The single git operation register needs -- injected, never defaulted,
    so this module never clones or hits the network itself."""

    def commit_exists(self, repo: str, sha: str) -> bool: ...


class RegisterError(Exception):
    """Base for every register-ladder rejection."""


class WrongOwner(RegisterError):
    """Identity: the resolved login doesn't own ``reference.repo``."""


class MissingCommit(RegisterError):
    """Reference: ``reference.commit`` isn't a 40-hex sha the repo has."""


class BadManifest(RegisterError):
    """Manifest: ``manifest`` is missing/malformed."""


class UnknownObjective(RegisterError):
    """Evidence: ``evidence.objective.seed_set`` isn't a published catalog objective."""


class WrongBatch(RegisterError):
    """Evidence: the submitted ``(seed, character)`` set isn't the objective's batch."""


class NonFiniteMetrics(RegisterError):
    """Evidence: some result's ``progress`` is NaN or infinite."""


class MissingImage(RegisterError):
    """Evidence: ``evidence.evaluator_image`` is empty."""


class WrongTier(RegisterError):
    """Evidence: ``evidence.tier`` isn't ``"self-reported"`` (the only tier register writes)."""


def register(
    store: Store,
    auth: AuthProvider,
    *,
    token: str,
    reference: SolutionReference,
    manifest: dict[str, Any],
    evidence: Evidence,
    git: CommitChecker,
    now: str,
) -> RegisterResult:
    """Run the validation ladder and, only on full success, store the solution
    + atoms and recompute the derived views. Each rejection raises its own
    ``RegisterError`` subclass before anything is written; ``AuthError`` from
    ``auth.resolve`` propagates unchanged (the API maps it to 401).

    Re-registering identical evidence is a safe no-op: ``insert_atoms`` dedups
    and every write below is independently idempotent. The solution is keyed by
    ``<repo>@<commit>`` (not a content digest); atoms are keyed to that id.
    """
    # 1. identity -- the token's login owns the repo.
    login = auth.resolve(token)
    segments = reference.repo.rstrip("/").split("/")
    if len(segments) < 2 or not owns_repo(login, reference.repo):
        raise WrongOwner(f"{login!r} does not own {reference.repo!r}")

    # 2. reference exists (a real, full sha) -- so the link is fetchable.
    if _COMMIT_RE.fullmatch(reference.commit) is None or not git.commit_exists(
        reference.repo, reference.commit
    ):
        raise MissingCommit(f"{reference.repo}@{reference.commit} not found")

    # 3. manifest well-formed (carried in the request; no content-digest check).
    manifest = _validated_manifest(manifest)

    # 4. evidence well-formed -- a published objective, its exact batch, finite
    #    metrics, an evaluator image, and the self-reported tier.
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

    # 5. store & recompute -- reached only once every check above has passed.
    solution_id = f"{reference.repo}@{reference.commit}"
    store.objectives_upsert(spec)
    store.upsert_solution(
        solution_id,
        repo=reference.repo,
        commit_sha=reference.commit,
        owner=login,
        root=manifest["root"],
        entrypoint=manifest["entrypoint"],
        registered_at=now,
    )
    for parent in manifest.get("parents", []):
        store.add_lineage(solution_id, parent, "parent")
    for influence in manifest.get("influences", []):
        store.add_lineage(solution_id, influence, "influence")

    atoms = evidence_to_atoms(evidence, owner=login, solution_id=solution_id)
    inserted = store.insert_atoms(atoms)
    update_attainment(store, atoms, now=now)
    recompute_elites(store)

    return RegisterResult(
        solution_id=solution_id, owner=login, objective=name, atoms_inserted=inserted
    )


def _validated_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Raise ``BadManifest`` unless ``manifest`` is a dict with non-empty
    string ``root`` and ``entrypoint``, and (if present) ``parents`` /
    ``influences`` each a list of strings. Returns it unchanged, type-narrowed."""
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
