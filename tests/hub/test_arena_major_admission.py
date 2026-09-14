"""Admission maps a submitted digest to a major instead of comparing bytes."""

import logging

import pytest

from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.store import Store
from nethackers.hub.validate import SolutionReference
from nethackers.hub.verify import ParityMismatch, _check_hidden_evidence, record_attempt

OLD_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142"
)
NEW_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3"
)
UNKNOWN_IMAGE = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "0" * 64
# What eval/runner.py's _default_image_digest reports for a locally built
# arena that was never pushed: a bare image Id with no digest portion.
BARE_IMAGE_ID = "sha256:" + "1" * 64

SECRET = "hidden-key"
SEEDS = (4839201, 1029384)
REPO = "github.com/sam/nethacker"
SHA = "a" * 40


def _evidence(image, identity="val-dwa-law-fem"):
    """Built exactly as tests/hub/test_verify.py builds it, differing only in
    the image under test."""
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4,
                         ascended=False, steps=1, turns=1, max_depth=1,
                         end_status="died", error=None, wall_seconds=0.1,
                         character=identity, milestone=None)
        for sd in SEEDS)
    return Evidence.from_results(
        solution_digest=f"{REPO}@{SHA}",
        objective=Objective(character=None, seed_set="v"),
        evaluator_image=image, results=results, created_at="t")


def _check(image, current_major=1):
    _check_hidden_evidence(
        _evidence(image), secret_fingerprint=secret_fingerprint(SECRET),
        current_major=current_major, hub_secret=SECRET, seeds=SEEDS,
    )


def test_a_classified_digest_at_the_current_major_is_admitted():
    _check(OLD_IMAGE)
    _check(NEW_IMAGE)   # a different digest, the same major


def test_an_unclassified_digest_is_rejected():
    with pytest.raises(ParityMismatch, match="not a classified arena image"):
        _check(UNKNOWN_IMAGE)


def test_a_classified_digest_at_an_older_major_names_both_majors():
    with pytest.raises(ParityMismatch, match="arena major 1.*major 2"):
        _check(OLD_IMAGE, current_major=2)


def _record(store, image, *, status="failed", failure_kind="crashed"):
    record_attempt(
        store, reference=SolutionReference(repo=REPO, commit=SHA),
        secret_fingerprint=secret_fingerprint(SECRET), evaluator_image=image,
        verifier_token_fingerprint="tokenfp", status=status,
        failure_kind=failure_kind, message="boom", identities_done=0,
        now="2026-09-13T00:00:00Z")


def test_record_attempt_rejects_an_unclassified_image(tmp_path):
    """record_attempt derives the major from the reported image, so an image
    with no major has no truthful scope to file the attempt under -- a guessed
    one would let ``verify_candidates`` skip a program over a failure that
    never happened in that scope (invariant I2). Both unclassifiable shapes
    reach this: an arena pin from before the current one, and the bare image Id
    a locally built arena resolves to, which no map entry can ever classify."""
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    for image in (UNKNOWN_IMAGE, BARE_IMAGE_ID):
        with pytest.raises(ParityMismatch, match="not a classified arena image"):
            _record(store, image)
    assert store._conn.execute(
        "SELECT COUNT(*) FROM verified_attempts").fetchone()[0] == 0


def test_record_attempt_logs_the_rejection_it_raises(tmp_path, caplog):
    """The only production caller is POST /verify/attempts, and the worker
    daemon swallows that 400 in its per-candidate `except Exception: continue`.
    Without a server-side log the operator loses both the attempt row they used
    to get and any trace that a submission was refused at all."""
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    with (caplog.at_level(logging.WARNING, logger="nethackers.hub.verify"),
          pytest.raises(ParityMismatch)):
        _record(store, BARE_IMAGE_ID)
    messages = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING]
    assert any(BARE_IMAGE_ID in m for m in messages), messages
    assert any(REPO in m for m in messages), (
        f"the log has to name which program was refused: {messages!r}")


def test_record_attempt_files_a_wrong_major_image_rather_than_rejecting_it(tmp_path):
    """Deliberate asymmetry with _check_hidden_evidence, which 400s the
    EVIDENCE for this same submission. An attempt is an audit record of what
    actually ran; filing it at the major it genuinely ran under is truthful and
    self-limiting, because verify_candidates reads the hub's own major and so
    can never see it. Inert, not wrong -- and the map is what makes it
    classifiable in the first place, which is why an unclassified image (above)
    is the case that raises."""
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    _record(store, OLD_IMAGE)     # classified at major 1
    assert store.latest_verified_attempt(
        f"{REPO}@{SHA}", secret_fingerprint=secret_fingerprint(SECRET),
        arena_major=1) is not None
    # A hub at major 2 would never read it back.
    assert store.latest_verified_attempt(
        f"{REPO}@{SHA}", secret_fingerprint=secret_fingerprint(SECRET),
        arena_major=2) is None
