"""Admission maps a submitted digest to a major instead of comparing bytes."""

import pytest

from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.verify import ParityMismatch, _check_hidden_evidence

OLD_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142"
)
NEW_IMAGE = (
    "ghcr.io/dunnolab/nethackers-arena@sha256:"
    "d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3"
)
UNKNOWN_IMAGE = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "0" * 64

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
