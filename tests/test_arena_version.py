"""The arena major map, and the forcing function that keeps it honest."""

from nethackers import _image_pins
from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena_version import (
    ARENA_MAJOR,
    ARENA_MAJOR_BY_DIGEST,
    major_for,
)

UNKNOWN_DIGEST = "sha256:" + "0" * 64

AMD64_MAJOR_2 = (
    "sha256:862434c5e719941a3ef63a01449dfe4ef7c158790b15ab9628d39ecd336796eb"
)


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


def test_the_pinned_arena_classifies_at_the_current_major():
    """The single invariant that keeps registration working at all: if the pin
    is not classified at ARENA_MAJOR, every submission is refused."""
    assert major_for(_image_pins.ARENA_IMAGE) == ARENA_MAJOR


def test_major_2_is_the_amd64_manifest_digest():
    assert ARENA_MAJOR == 2
    assert ARENA_MAJOR_BY_DIGEST[AMD64_MAJOR_2] == 2


def test_the_major_1_entries_are_untouched():
    """Spec I3: once rows exist under a digest, its major is never edited."""
    assert (
        ARENA_MAJOR_BY_DIGEST[
            "sha256:9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142"
        ]
        == 1
    )
    assert (
        ARENA_MAJOR_BY_DIGEST[
            "sha256:d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3"
        ]
        == 1
    )
