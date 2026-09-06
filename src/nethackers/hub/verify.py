"""Verifier auth boundary for the verified tier (Task 4), plus (Task 5) the
verified-tier write path. ``VerifierConfig`` holds the hidden-eval secret,
hidden seeds, and the set of tokens a verifier node may present;
``resolve_verifier`` checks a presented token against that set and returns
its fingerprint (never the raw token) for storage/lookup.

``register_verified`` is ``hub.validate.register``'s verified-tier sibling --
a PARALLEL writer into the isolated ``verified_atoms`` table, never the
self-reported ``atoms`` table ``register`` owns (and ``register`` is not
reused: it hardcodes ``tier == "self-reported"``). Its ladder is shorter
than ``register``'s because identity/ownership is already settled -- a
solution only reaches ``/verify`` after it was already ``/register``-ed, so
``register_verified`` just looks up that row's ``owner`` rather than
re-resolving a caller token against GitHub. What it checks instead is
specific to hidden-eval trust: the evidence was produced under the pinned
arena image (parity), the submitted secret fingerprint is the hub's CURRENT
hidden secret (not a stale one from before a rotation), and every result
sits inside the hidden identity x seed spec. Atoms are always forced to
``tier="verified"`` via ``dataclasses.replace`` -- the caller's own
``evidence.tier`` is never trusted -- the same pattern
``baseline_compute.compute_baseline`` uses to force ``tier="baseline"``.

Kept minimal -- later tasks (6/7) extend this module further (attempt
bookkeeping, batching) on top of this write path.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace

from nethackers.arena.seeds import secret_fingerprint as _fingerprint
from nethackers.contracts.models import Evidence
from nethackers.hub.atoms import evidence_to_atoms
from nethackers.hub.ids import program_id as _program_id
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.validate import SolutionReference


@dataclass(frozen=True)
class VerifierConfig:
    tokens: frozenset[str]
    secret: str
    seeds: tuple[int, ...]


class VerifierAuthError(Exception):
    """Presented token is not a configured verifier token."""


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_verifier(token: str, cfg: VerifierConfig) -> str:
    if token not in cfg.tokens:
        raise VerifierAuthError("unknown verifier token")
    return token_fingerprint(token)


_IDENTITY_SET = frozenset(IDENTITIES)


class VerifyError(Exception):
    """Base for every register_verified rejection."""


class UnknownSolution(VerifyError):
    """``reference`` has no matching row -- the solution was never (or not yet)
    registered via ``POST /register``."""


class ParityMismatch(VerifyError):
    """``evidence.evaluator_image`` isn't the pinned arena image -- the
    evidence wasn't produced under the parity-enforced sandbox."""


class StaleSecret(VerifyError):
    """The submitted ``secret_fingerprint`` doesn't match the hub's current
    hidden secret (e.g. a batch computed before a secret rotation)."""


class BadBatch(VerifyError):
    """Some result's ``(character, trajectory_id)`` falls outside the hidden
    identity x seed spec (``IDENTITIES`` x the configured ``seeds``)."""


class NonFiniteMetrics(VerifyError):
    """Some result's ``progress`` is NaN or infinite."""


@dataclass(frozen=True)
class VerifyResult:
    """What a successful ``register_verified`` reports back. ``done``/
    ``total`` are the solution's coverage over the FULL hidden identity x
    seed grid for this secret epoch/image (``len(IDENTITIES) * len(seeds)``),
    not just this call's own batch -- so a caller can track progress across
    repeated ``/verify`` calls, not only the size of the latest one."""

    solution_id: str
    owner: str
    inserted: int
    done: int
    total: int


def record_attempt(store, *, reference, secret_fingerprint, evaluator_image,
                   verifier_token_fingerprint, status, failure_kind, message,
                   identities_done, now):
    """Record a program-level verification attempt (success or failure)."""
    store.insert_verified_attempt(
        solution_digest=f"{reference.repo}@{reference.commit}",
        secret_fingerprint=secret_fingerprint,
        evaluator_image=evaluator_image,
        verifier_token_fingerprint=verifier_token_fingerprint,
        status=status, failure_kind=failure_kind, message=message,
        identities_done=identities_done, at=now)


def register_verified(
    store: Store,
    *,
    reference: SolutionReference,
    evidence: Evidence,
    secret_fingerprint: str,
    verifier_token_fingerprint: str,
    now: str,
    expected_image: str,
    hub_secret: str,
    seeds: tuple[int, ...],
) -> VerifyResult:
    """Run the verified-tier ladder and, only on full success, write
    ``verified_atoms``. Each rejection raises its own ``VerifyError``
    subclass before anything is written, in order: solution exists
    (``UnknownSolution``); evidence ran under the pinned arena image
    (``ParityMismatch``); the submitted secret fingerprint matches the
    hub's current hidden secret (``StaleSecret``); every result's
    ``(character, trajectory_id)`` is inside the hidden spec
    (``BadBatch``); every result's progress is finite
    (``NonFiniteMetrics``).

    ``now`` is accepted for signature symmetry with ``register`` (a later
    task's attempt-bookkeeping write uses it); unused here.

    Idempotent like ``register``/``insert_atoms``: ``insert_verified_atoms``
    dedups on its own unique key, so re-submitting the same batch is a safe
    no-op (``inserted`` just comes back 0 the second time).
    """
    del now
    solution_id = f"{reference.repo}@{reference.commit}"
    row = store.get_solution(solution_id)
    if row is None:
        raise UnknownSolution(f"{solution_id} is not registered")

    if evidence.evaluator_image != expected_image:
        raise ParityMismatch(
            f"evaluator_image {evidence.evaluator_image!r} != pinned arena image {expected_image!r}"
        )

    if secret_fingerprint != _fingerprint(hub_secret):
        raise StaleSecret("secret_fingerprint does not match the current hidden secret")

    seed_set = frozenset(seeds)
    for result in evidence.results:
        if result.character not in _IDENTITY_SET or result.trajectory_id not in seed_set:
            raise BadBatch(
                f"({result.character}, {result.trajectory_id}) is outside the hidden spec"
            )

    if not all(math.isfinite(result.progress) for result in evidence.results):
        raise NonFiniteMetrics("some result's progress is not finite")

    owner = row["owner"]
    atoms = [
        replace(atom, tier="verified")
        for atom in evidence_to_atoms(evidence, owner=owner, solution_id=solution_id)
    ]
    inserted = store.insert_verified_atoms(
        atoms, secret_fingerprint=secret_fingerprint,
        verifier_token_fingerprint=verifier_token_fingerprint,
    )

    covered = [
        atom
        for atom in store.iter_verified_atoms(
            solution_digest=solution_id, secret_fingerprint=secret_fingerprint,
            evaluator_image=expected_image,
        )
        if atom.seed in seed_set
    ]
    total = len(IDENTITIES) * len(seeds)
    return VerifyResult(
        solution_id=solution_id, owner=owner, inserted=inserted,
        done=len(covered), total=total,
    )


# Attempt outcomes that will never resolve on retry against the same
# solution/image (a bad build, a crash, a hang) -- as opposed to a
# transient/environmental failure. verify_candidates uses this to stop
# offering a solution that has already failed deterministically, rather
# than handing it back to a verifier node forever.
_DETERMINISTIC = frozenset({"build_failed", "crashed", "hung"})


def verify_candidates(store, *, secret_fingerprint, evaluator_image, seeds, limit):
    """Programs still needing verified coverage under this secret epoch and
    evaluator image, least-covered first -- the verifier node's work queue
    (``GET /verify/candidates``, Tasks 10/11's polling loop).

    Skips a solution whose ``latest_verified_attempt`` failed
    deterministically (``_DETERMINISTIC``) -- retrying it would just fail
    again -- and one already fully covered (``done >= total`` over the
    hidden identity x seed grid). Each surviving row is
    ``{"program_id", "reference": {"repo", "commit"},
    "coverage": {"done", "total"}}``, sorted by ``(done, program_id)`` so
    the least-covered (and, among ties, lowest program_id) programs come
    first; only the first ``limit`` are returned.
    """
    seed_set = frozenset(seeds)
    total = len(IDENTITIES) * len(seeds)
    out = []
    for sol in store.iter_solutions():
        digest = sol["digest"]
        latest = store.latest_verified_attempt(
            digest, secret_fingerprint=secret_fingerprint, evaluator_image=evaluator_image
        )
        if latest and latest["status"] == "failed" and latest["failure_kind"] in _DETERMINISTIC:
            continue
        atoms = store.iter_verified_atoms(
            solution_digest=digest, secret_fingerprint=secret_fingerprint,
            evaluator_image=evaluator_image,
        )
        done = len([a for a in atoms if a.seed in seed_set])
        if done >= total:
            continue
        out.append({
            "program_id": _program_id(digest),
            "reference": {"repo": sol["repo"], "commit": sol["commit_sha"]},
            "coverage": {"done": done, "total": total},
        })
    out.sort(key=lambda r: (r["coverage"]["done"], r["program_id"]))
    return out[:limit]
