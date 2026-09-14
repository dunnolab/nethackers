"""The arena major map, and the forcing function that keeps it honest."""

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena_version import (
    ARENA_MAJOR,
    ARENA_MAJOR_BY_DIGEST,
    major_for,
)

UNKNOWN_DIGEST = "sha256:" + "0" * 64


def test_pinned_image_is_classified_at_the_current_major():
    """The forcing function (spec 5.4). A re-pin that nobody classified fails
    here, on the branch that re-pinned, and the fix is to add a line to the map
    with an explicit major -- which is where the human judgment belongs."""
    assert major_for(ARENA_IMAGE) == ARENA_MAJOR


def test_every_entry_is_a_bare_digest():
    """Keys are digests, not full refs, so the registry prefix cannot drift."""
    for key in ARENA_MAJOR_BY_DIGEST:
        assert key.startswith("sha256:")
        assert "@" not in key and "/" not in key


def test_major_for_ignores_the_registry_prefix():
    digest, major = next(iter(ARENA_MAJOR_BY_DIGEST.items()))
    assert major_for(f"example.invalid/some-mirror@{digest}") == major


def test_major_for_returns_none_for_an_unclassified_digest():
    assert major_for(f"ghcr.io/dunnolab/nethackers-arena@{UNKNOWN_DIGEST}") is None


def test_major_for_returns_none_for_a_tag():
    """Only content-addressed refs can be classified; a tag names movable bytes."""
    assert major_for("ghcr.io/dunnolab/nethackers-arena:dev") is None
