"""Hub register write-path: the validation ladder that turns a submission
(a public ``repo@commit`` link + its per-identity self-reported evidence) into
a stored solution + atoms + recomputed views.

Reconciles two models:

- the **link identity** (M1): a submission is a public ``repo@commit`` the
  caller owns; the pinned commit SHA *is* the solution identity
  (``repo@commit``), so it is fetchable and no content digest is computed or
  verified here.
- the **atom model** (v1 / generalist objectives): the submission carries
  ``Evidence`` (a batch of per-identity results) in a single call spanning
  the whole identity set's canonical union batch; ``evidence_to_atoms`` keys
  each atom by its own result's identity (``tier="self-reported"``), which
  the boards / attainment / elite views aggregate. So one solution row grows
  atoms across the whole identity set from that one registration.

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
from dataclasses import dataclass, replace
from typing import Any, Protocol

from nethackers.arena_version import ARENA_MAJOR, major_for
from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.github_ref import NonGitHubRef, normalize_github_ref
from nethackers.hub.atoms import evidence_to_atoms
from nethackers.hub.auth import AuthProvider, owns_repo
from nethackers.hub.ids import program_id as _program_id
from nethackers.hub.objectives import CATALOG, IDENTITIES, build_union_spec
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment

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
    program_id: str


class CommitChecker(Protocol):
    """The single git operation register needs -- injected, never defaulted,
    so this module never clones or hits the network itself."""

    def commit_exists(self, repo: str, sha: str) -> bool: ...


class RegisterError(Exception):
    """Base for every register-ladder rejection."""


class NonGitHubRepo(RegisterError):
    """Identity: ``reference.repo`` isn't an unambiguous github.com/<owner>/<name>.

    Translates ``github_ref.NonGitHubRef`` (a bare ``ValueError`` -- that leaf
    module imports neither ``hub`` nor ``hubclient``, so it can't subclass
    this) into the register ladder's own exception hierarchy, so
    ``hub/api.py``'s existing ``except RegisterError`` maps it to a clean 400
    instead of an unhandled 500.
    """


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


class UnclassifiedArena(RegisterError):
    """Evidence: ``evidence.evaluator_image`` is not a classified arena image.

    Typically a locally built tag. A tag names movable bytes, so it is
    unclassified by construction and can never be admitted.
    """


class WrongArenaMajor(RegisterError):
    """Evidence: ``evidence.evaluator_image`` classifies to a retired arena
    major, not the one this hub currently accepts."""


def classified_major(image: str, current_major: int) -> None:
    """Admit ``image`` only if it classifies to ``current_major``.

    The single admission rule for BOTH tiers (spec 2026-09-14 D5, I5'). Reads
    never consult the major; admitting evidence to any tier always does.
    ``hub/verify.py`` wraps the two failures in its own ``ParityMismatch`` so
    the verified tier's callers see an unchanged exception type.
    """
    submitted = major_for(image)
    if submitted is None:
        raise UnclassifiedArena(
            f"evaluator_image {image!r} is not a classified arena image -- "
            f"evidence must come from the pinned arena image, not a local build. "
            f"If you set NETHACKERS_ARENA_IMAGE (a local stack's .env.stack sets "
            f"it to this worktree's own arena tag), unset it and re-run"
        )
    if submitted != current_major:
        raise WrongArenaMajor(
            f"evaluator_image {image!r} is arena major {submitted}, but this hub "
            f"is on major {current_major} -- upgrade the nethackers CLI"
        )


_IDENTITY_SET = frozenset(IDENTITIES)


def _objective_and_batch(
    name: str, submitted: set[tuple[int, str]]
) -> tuple[ObjectiveSpec, set[tuple[int, str]]] | None:
    """Resolve the evidence's objective name to (spec, canonical batch).

    A catalog objective (a single identity, or a legacy name still in the
    catalog) -> its own spec/batch. Otherwise a SET objective: reconstruct the
    member identities from the submitted evidence's distinct characters and
    build their canonical union batch. Returns ``None`` for an unknown
    objective (a character that isn't a published identity)."""
    spec = CATALOG.get(name)
    if spec is not None:
        return spec, set(spec.batch)
    identities = sorted({character for _seed, character in submitted})
    if not identities or any(i not in _IDENTITY_SET for i in identities):
        return None
    union = build_union_spec(identities, name=name)
    return union, set(union.batch)


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
    # reference host -- must be an unambiguous github.com/<owner>/<name>
    # (raises NonGitHubRepo). Normalizing here, before owns_repo/commit_exists
    # ever run, is the authoritative server-side gate: owns_repo only compares
    # the owner segment (blind to host), so a non-github URL sharing the
    # caller's login as its owner segment would otherwise sail through.
    # github_ref.py's leaf NonGitHubRef is translated to our own
    # NonGitHubRepo(RegisterError) so hub/api.py's existing "except
    # RegisterError" maps this to a 400 like every other rejection here,
    # instead of an unhandled 500.
    try:
        reference = replace(reference, repo=normalize_github_ref(reference.repo))
    except NonGitHubRef as e:
        raise NonGitHubRepo(str(e)) from e
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
    submitted = {(r.trajectory_id, r.character) for r in evidence.results}
    resolved = _objective_and_batch(name, submitted)
    if resolved is None:
        raise UnknownObjective(f"{name!r} is not a published catalog objective or identity set")
    _spec, canonical_batch = resolved
    if submitted != canonical_batch:
        raise WrongBatch(f"submitted batch does not match {name!r}'s canonical batch")
    if not all(math.isfinite(r.progress) for r in evidence.results):
        raise NonFiniteMetrics("some result's progress is not finite")
    if not evidence.evaluator_image:
        raise MissingImage("evaluator_image is required")
    classified_major(evidence.evaluator_image, ARENA_MAJOR)
    if evidence.tier != "self-reported":
        raise WrongTier(f"tier {evidence.tier!r} is not self-reported")

    # 5. store -- reached only once every check above has passed. atoms +
    # attainment are all this writes now: /elites is a live query over
    # atoms (Part 2 of the hub API redesign dropped elite_pool + the
    # recompute step that used to run here).
    solution_id = f"{reference.repo}@{reference.commit}"
    store.upsert_solution(
        solution_id,
        repo=reference.repo,
        commit_sha=reference.commit,
        owner=login,
        root=manifest.get("root", "."),
        entrypoint=manifest.get("entrypoint", "bot.py"),
        registered_at=now,
    )
    for parent in manifest.get("parents", []):
        store.add_lineage(solution_id, parent, "parent")
    for influence in manifest.get("influences", []):
        store.add_lineage(solution_id, influence, "influence")

    atoms = evidence_to_atoms(evidence, owner=login, solution_id=solution_id)
    inserted = store.insert_atoms(atoms)
    update_attainment(store, atoms, now=now)

    return RegisterResult(
        solution_id=solution_id, owner=login, objective=name, atoms_inserted=inserted,
        program_id=_program_id(solution_id),
    )


def _validated_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Raise ``BadManifest`` unless ``manifest`` is a dict whose (if present)
    ``parents`` / ``influences`` are each a list of strings. ``root`` and
    ``entrypoint`` are optional -- ``register`` defaults them when absent.
    Returns it unchanged, type-narrowed."""
    if not isinstance(manifest, dict):
        raise BadManifest("manifest missing or not an object")
    for key in ("parents", "influences"):
        if key not in manifest:
            continue
        value = manifest[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BadManifest(f"manifest.{key} must be a list of strings")
    return manifest
