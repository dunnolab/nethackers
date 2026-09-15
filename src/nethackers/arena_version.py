"""The arena image's SCORING identity, as distinct from its content digest.

A digest answers "which exact bytes ran". It is the wrong key for "are these
two runs comparable", because a rebuild that only changes process lifetime,
logging or an error message produces new bytes and identical scores. Keying the
verified tier on the digest therefore discarded the entire verified corpus on
every quality-of-life rebuild (design doc
``docs/superpowers/specs/2026-09-13-arena-major-version-design.md`` section 1).

``ARENA_MAJOR`` is the missing key: an integer naming a set of digests declared
to score alike. It is HAND-ASSERTED. A human decides whether a rebuild moved
scores and records that decision here, reviewed in the PR that made the
rebuild. There is no behavioral gate (design D3), so every entry below MUST
carry a comment saying what the rebuild changed and why it did or did not move
scores. Those comments are the entire audit trail.

Bump ``ARENA_MAJOR`` only when scores move. A rebuild that does not move them
is a new line in the map at the current major. Never edit an existing digest's
major once rows exist under it (design invariant I3) -- that silently moves
history from one board to another.

Hand-edited, and deliberately NOT part of ``_image_pins.py``, which
``scripts/repin_images.py`` rewrites wholesale and would clobber (design D5).
"""

from __future__ import annotations

ARENA_MAJOR = 2

ARENA_MAJOR_BY_DIGEST: dict[str, int] = {
    # The image every existing verified row was measured under.
    "sha256:9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142": 1,
    # PR #66: PR_SET_PDEATHSIG armed on episode pool workers and on the bot
    # process, so neither outlives a SIGKILLed driver. Process lifetime only --
    # nothing on the path that seeds, steps or scores an episode -- so it stays
    # comparable with the digest above (design D7).
    "sha256:d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3": 1,
    # PR #67: the OpenCode 2 operator, merged with main at 0.24.1. Everything
    # that differs from the digest above's build sits off the scoring path:
    # harness/, tui/, eval/, hub/, a few top-level modules, README.md and the
    # pyproject version. arena/ and contracts/ import nothing outside
    # themselves and are unchanged, as are uv.lock and nle-base, so it stays
    # comparable with the digest above. Read from the diff, not measured.
    "sha256:97ba883e04fd79d334c9074bfd92bfe8b0c923a0882881bef5435b369b6a1601": 1,
    # THE amd64 REFERENCE (design 2026-09-14-amd64-reference-reset). This is the
    # linux/amd64 LEG of the index digest directly above, and it is major 2
    # while that index is major 1. That is deliberate, not a mistake: an index
    # resolves to whichever platform the host is, so evidence produced through
    # it comes from the mixed-architecture population major 1 describes. This
    # leg pins every host to amd64, which is a different comparability class.
    # SCORES MOVE across that boundary because NetHack consumes RNG in
    # unsequenced sibling call arguments (e.g. mkgold(0L, somex(c), somey(c)) at
    # src/mklev.c:831) and gcc orders call arguments left-to-right on aarch64
    # and right-to-left on x86-64, so one seed generates a different dungeon per
    # architecture. Measured: the monk elite scores 0.15494 on arm64 and 0.10994
    # on x86_64 over the same published batch. Measured again when adopting this
    # leg: all 15 published monk starting maps are identical to the leg the
    # design was written against (862434c5...), so PR #67's "not measured"
    # equivalence claim above holds for amd64.
    "sha256:5c8c0cee2f28d2a0de5e9b78170a01ec0991a18a9802de0a43e8e312590d151c": 2,
}


def major_for(image: str) -> int | None:
    """The arena major for a full image ref, or ``None`` if unclassified.

    Matches on the digest portion, so a registry rename or a mirror of the same
    bytes resolves identically and never invalidates the map. A ref carrying no
    ``@<digest>`` part (a tag) is unclassified by construction: a tag names
    movable bytes, and only content-addressed bytes can be declared comparable.
    """
    _, separator, digest = image.partition("@")
    if not separator:
        return None
    return ARENA_MAJOR_BY_DIGEST.get(digest)
