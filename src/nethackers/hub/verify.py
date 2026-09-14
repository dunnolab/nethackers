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
specific to hidden-eval trust: the evidence ran under an arena image
classified at the hub's current arena major (parity), the submitted secret
fingerprint is the hub's CURRENT hidden secret (not a stale one from before
a rotation), and every result sits inside the hidden identity x seed spec.
Atoms are always forced to ``tier="verified"`` via ``dataclasses.replace``
-- the caller's own ``evidence.tier`` is never trusted -- the same pattern
``baseline_compute.compute_baseline`` uses to force ``tier="baseline"``.

``register_verified_baseline`` is the third writer here: AutoAscend's
hidden-seed reference floor, into its own ``verified_baseline_atoms`` table.
It shares the hidden-eval trust ladder (``_check_hidden_evidence``) with
``register_verified`` but skips the solution lookup entirely -- the floor has
no ``solutions`` row and must never get one, or it would rank as a participant
against the programs it exists to measure.

Kept minimal -- later tasks (6/7) extend this module further (attempt
bookkeeping, batching) on top of this write path.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, replace
from typing import Any

from nethackers.arena.seeds import secret_fingerprint as _fingerprint
from nethackers.arena_version import major_for
from nethackers.contracts.models import Evidence
from nethackers.hub.atoms import evidence_to_atoms
from nethackers.hub.ids import AUTOASCEND_ID, program_id as _program_id
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    SolutionReference,
    UnclassifiedArena,
    WrongArenaMajor,
    classified_major,
)

logger = logging.getLogger(__name__)


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
    """``evidence.evaluator_image`` doesn't resolve to the hub's current
    arena major -- either the digest is unclassified (``arena_version`` has
    no entry for it) or it classifies to a different, older major than the
    one this hub currently accepts."""


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


@dataclass(frozen=True)
class BaselineVerifyResult:
    """What a successful ``register_verified_baseline`` reports back.
    ``done``/``total`` are the FLOOR's coverage over the full hidden identity
    x seed grid for this epoch -- the same shape ``VerifyResult`` reports for
    a participant, so a caller tracks a multi-call baseline run the same way
    it tracks a multi-call verification. There is no ``owner``/``solution_id``
    because the floor has neither: it is always AutoAscend."""

    inserted: int
    done: int
    total: int


def _check_hidden_evidence(evidence: Evidence, *, secret_fingerprint: str,
                           current_major: int, hub_secret: str,
                           seeds: tuple[int, ...]) -> None:
    """The trust ladder every hidden-seed submission must clear, participant
    or floor, in order: the evidence ran under an arena image classified at
    this hub's current major (``ParityMismatch``); it was produced under the
    hub's CURRENT hidden secret, not a stale one from before a rotation
    (``StaleSecret``); every result sits inside the hidden identity x seed spec
    (``BadBatch``); every result's progress is finite (``NonFiniteMetrics``).
    Raises before the caller writes anything -- a rejected submission stores
    nothing.

    The parity rung maps the submitted digest through
    ``arena_version.major_for`` rather than comparing it to the hub's own pin.
    A node one release behind, running a different digest of the same major,
    is therefore still admitted -- which is the point: it removes the lockstep
    requirement between the hub and the evaluator node, and it is what stops a
    quality-of-life rebuild from orphaning the corpus (design D6).
    """
    # One admission rule for both tiers (spec 2026-09-14 D5). Re-raised as
    # ParityMismatch so this module's callers keep their exception contract.
    try:
        classified_major(evidence.evaluator_image, current_major)
    except (UnclassifiedArena, WrongArenaMajor) as error:
        raise ParityMismatch(str(error)) from error

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


def register_verified_baseline(
    store: Store,
    *,
    evidence: Evidence,
    secret_fingerprint: str,
    verifier_token_fingerprint: str,
    current_major: int,
    hub_secret: str,
    seeds: tuple[int, ...],
) -> BaselineVerifyResult:
    """Write AutoAscend's hidden-seed floor into the isolated
    ``verified_baseline_atoms`` table.

    ``register_verified``'s sibling, and deliberately a shorter ladder: it
    skips the ``solutions`` lookup entirely because AutoAscend has no
    ``solutions`` row and must never acquire one -- a row would make the
    reference floor rank as a participant against the programs it exists to
    measure. Ownership is therefore the constant ``AUTOASCEND_ID`` rather
    than something resolved from the hub, and ``tier`` is forced to
    ``"baseline"`` via ``replace`` -- the caller's ``evidence.tier`` is never
    trusted, mirroring how ``register_verified`` forces ``"verified"``.

    Everything trust-bearing about hidden-seed evidence is still enforced,
    via the shared ``_check_hidden_evidence`` ladder: parity, secret epoch,
    batch membership, finite metrics. A floor submitted from an unclassified
    image or the wrong arena major or off-spec seeds would silently corrupt
    every Delta-vs-AA on the board, so it is rejected exactly as a
    participant's would be.

    Idempotent: ``insert_verified_baseline_atoms`` dedups on its own unique
    key, so a re-run under the same epoch reports ``inserted == 0`` while
    ``done`` still reports true coverage.
    """
    _check_hidden_evidence(evidence, secret_fingerprint=secret_fingerprint,
                           current_major=current_major, hub_secret=hub_secret, seeds=seeds)

    atoms = [
        replace(atom, tier="baseline")
        for atom in evidence_to_atoms(evidence, owner=AUTOASCEND_ID,
                                      solution_id=AUTOASCEND_ID)
    ]
    inserted = store.insert_verified_baseline_atoms(
        atoms, secret_fingerprint=secret_fingerprint,
        verifier_token_fingerprint=verifier_token_fingerprint,
        arena_major=current_major,
    )

    seed_set = frozenset(seeds)
    covered = [
        atom
        for atom in store.iter_verified_baseline_atoms(
            secret_fingerprint=secret_fingerprint, arena_major=current_major
        )
        if atom.seed in seed_set
    ]
    return BaselineVerifyResult(
        inserted=inserted, done=len(covered), total=len(IDENTITIES) * len(seeds)
    )


def record_attempt(
    store: Store, *, reference: SolutionReference, secret_fingerprint: str,
    evaluator_image: str, verifier_token_fingerprint: str, status: str,
    failure_kind: str | None, message: str | None, identities_done: int,
    now: str,
) -> None:
    """Record a program-level verification attempt (success or failure).

    The major is derived from the reported image rather than taken from the
    caller, so an attempt row lands in the same scope the evidence would have.
    An UNCLASSIFIED image raises: a row stored under a guessed major would make
    ``verify_candidates`` skip a program on the strength of a failure that
    never happened in this scope (design invariant I2).

    A WRONG-MAJOR image -- classified, but not at the hub's current major --
    is deliberately NOT rejected here, even though ``_check_hidden_evidence``
    rejects the evidence for that same submission. The asymmetry is the point,
    because the two rows do different jobs:

    - Evidence is scored data. Admitting it at the wrong major would put
      incomparable episodes on the live board, so it must fail loudly and the
      node operator must see the 400 and upgrade.
    - An attempt is an audit record of what actually happened on whichever node
      ran it. Storing it at the major it genuinely ran under is the truthful
      thing to do, and it is also self-limiting: ``verify_candidates`` reads
      the hub's own major, so an attempt filed at an older one can never
      suppress a candidate on the live board. It is inert rather than wrong.

    So a wrong-major attempt is a write into a scope nothing currently reads --
    which is exactly what an audit trail for a stale node should be. An
    unclassified image has no truthful scope to be filed under at all, which is
    why that one is the rejection.

    The rejection is logged before it is raised. The only production caller is
    ``POST /verify/attempts``, whose 400 the worker daemon swallows in its
    per-candidate ``except Exception: continue`` -- so without this line the
    operator loses both the attempt row they used to get AND any trace that a
    submission was refused.
    """
    arena_major = major_for(evaluator_image)
    if arena_major is None:
        logger.warning(
            "refusing a verification attempt for %s@%s: evaluator_image %r is "
            "not classified in ARENA_MAJOR_BY_DIGEST, so it has no arena major "
            "to file the attempt under. The reported attempt was %s (%s). "
            "Classify the digest in src/nethackers/arena_version.py or point "
            "the node at a classified arena image.",
            reference.repo, reference.commit, evaluator_image, status,
            failure_kind or "no failure_kind",
        )
        raise ParityMismatch(
            f"evaluator_image {evaluator_image!r} is not a classified arena image"
        )
    store.insert_verified_attempt(
        solution_digest=f"{reference.repo}@{reference.commit}",
        secret_fingerprint=secret_fingerprint,
        evaluator_image=evaluator_image,
        arena_major=arena_major,
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
    current_major: int,
    hub_secret: str,
    seeds: tuple[int, ...],
) -> VerifyResult:
    """Run the verified-tier ladder and, only on full success, write
    ``verified_atoms``. Each rejection raises its own ``VerifyError``
    subclass before anything is written, in order: solution exists
    (``UnknownSolution``); evidence ran under an arena image classified at
    this hub's current major (``ParityMismatch``); the submitted secret
    fingerprint matches the hub's current hidden secret (``StaleSecret``);
    every result's ``(character, trajectory_id)`` is inside the hidden spec
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

    _check_hidden_evidence(evidence, secret_fingerprint=secret_fingerprint,
                           current_major=current_major, hub_secret=hub_secret, seeds=seeds)
    seed_set = frozenset(seeds)

    owner = row["owner"]
    atoms = [
        replace(atom, tier="verified")
        for atom in evidence_to_atoms(evidence, owner=owner, solution_id=solution_id)
    ]
    inserted = store.insert_verified_atoms(
        atoms, secret_fingerprint=secret_fingerprint,
        verifier_token_fingerprint=verifier_token_fingerprint,
        arena_major=current_major,
    )

    covered = [
        atom
        for atom in store.iter_verified_atoms(
            solution_digest=solution_id, secret_fingerprint=secret_fingerprint,
            arena_major=current_major,
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


def verify_candidates(store, *, secret_fingerprint, arena_major: int, seeds, limit):
    """Programs still needing verified coverage under this secret epoch and
    arena major, least-covered first -- the verifier node's work queue
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
    out: list[dict[str, Any]] = []
    for sol in store.iter_solutions():
        digest = sol["digest"]
        latest = store.latest_verified_attempt(
            digest, secret_fingerprint=secret_fingerprint, arena_major=arena_major
        )
        if latest and latest["status"] == "failed" and latest["failure_kind"] in _DETERMINISTIC:
            continue
        atoms = store.iter_verified_atoms(
            solution_digest=digest, secret_fingerprint=secret_fingerprint,
            arena_major=arena_major,
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
