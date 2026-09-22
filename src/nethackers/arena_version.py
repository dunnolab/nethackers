"""The arena image's SCORING identity, as distinct from its content digest.

A digest answers "which exact bytes ran". It is the wrong key for "are these
two runs comparable", because a rebuild that only changes process lifetime,
logging or an error message produces new bytes and identical scores. Keying the
verified tier on the digest therefore discarded the entire verified corpus on
every quality-of-life rebuild.

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
    # The pre-release docs cleanup, rebuilt because uv.lock is an arena input.
    # The lock change is METADATA ONLY: it had drifted to describing the project
    # as 0.17.0 with `textual >=0.60` while pyproject said 0.31.0 / >=8.2.8, but
    # it already RESOLVED textual at 8.2.8, so not one package version moves and
    # the installed environment is identical. Everything else that reaches this
    # image -- README.md and a handful of modules under src/ -- is docstrings and
    # comments, with one JSON `description` string in doctor.schema.json.
    # arena/ and contracts/ are untouched since v0.31.0 (`git diff v0.31.0...HEAD
    # -- src/nethackers/arena src/nethackers/contracts` is empty), so nothing on
    # the path that seeds, steps or scores an episode differs from the digest
    # above. Also the linux/amd64 LEG, not an index, so the reference
    # architecture is preserved (I7). Read from the diff, not measured.
    "sha256:865eb2033dd31892418915fe44a15e78e2beefc88fe358f89864b0ffce6d3e42": 2,
    # v0.31.1, rebuilt because uv.lock is an arena input. Three files differ from
    # the digest above: the website page (src/nethackers/hub/web/index.html --
    # scratch-card behaviour, served by the hub, never loaded by an episode), and
    # the project's own version in pyproject.toml and uv.lock, 0.31.0 -> 0.31.1.
    # The lock delta is that one line; not a single package version moves, so the
    # installed environment is byte-identical. arena/ and contracts/ are untouched
    # (`git diff v0.31.0...HEAD -- src/nethackers/arena src/nethackers/contracts`
    # is empty), so nothing on the path that seeds, steps or scores an episode
    # differs. Also the linux/amd64 LEG, not an index, so the reference
    # architecture is preserved (I7). Read from the diff, not measured.
    "sha256:0c8715c6812ae24dd3b93ed79969c69e6bdc08513302c42b792bd593afc78ffe": 2,
    # v0.32.0, rebuilt because uv.lock is an arena input and the release moves
    # the project's own version in it, 0.31.1 -> 0.32.0; not one package version
    # moves, so the installed environment is identical. The other files that
    # differ from the digest above are host-side modules no episode loads
    # (cli, eval/runner, harness/, image_inputs, tui): arena/ and contracts/ are
    # untouched (`git diff v0.31.1...HEAD -- src/nethackers/arena
    # src/nethackers/contracts` is empty). nle-base was rebuilt from the same
    # Dockerfile. MEASURED, not only read from the diff: a registered knight
    # program on the 15 published kni-hum-law-fem seeds matches its record under
    # 5c8c0cee above -- progress, steps, turns and cause of death, 15/15. Also
    # the linux/amd64 LEG, not an index, so the reference architecture is
    # preserved (I7).
    "sha256:55b19fde2d35390e5cd23f3d5808aba04b9688e7125c608f2550f1e677a9d8c6": 2,
    # v0.32.1, rebuilt for the same reason as the digest above: uv.lock is an
    # arena input and the release moves the project's own version in it,
    # 0.32.0 -> 0.32.1. Everything else that differs is the TUI evolve form and
    # a comment in harness/launch.py, neither of which an episode loads;
    # arena/ and contracts/ are untouched (`git diff v0.32.0...HEAD --
    # src/nethackers/arena src/nethackers/contracts` is empty). MEASURED again:
    # the same knight program on the same 15 published kni-hum-law-fem seeds
    # scores identically to its record, seed for seed, in progress, turns,
    # depth, milestone and cause of death. One seed's agent-step count differs
    # by one (84419 vs 84420) -- that counter varies run to run within a single
    # image, so it is noise, not this rebuild. Also the linux/amd64 LEG (I7).
    "sha256:8f3595c3fb7bdb36edf8bf0e5502d7272ef00b299001804ea4d245451fcd651a": 2,
    # v0.32.2, rebuilt for the same reason as the two digests above: uv.lock is
    # an arena input and the release moves the project's own version in it,
    # 0.32.1 -> 0.32.2. Everything else that differs is the rootless-podman
    # userns fix (#54) in containers.py, harness/container_operator.py and
    # harness/launch.py -- host-side argv assembly for the MUTATOR cage, which
    # no episode loads and which is deliberately never applied to the arena.
    # arena/ and contracts/ are untouched (`git diff v0.32.1...HEAD --
    # src/nethackers/arena src/nethackers/contracts` is empty). MEASURED: the
    # same knight program on the same 15 published kni-hum-law-fem seeds scores
    # identically to its record, every field, all 15 -- this time including the
    # agent-step counter that wobbles between runs. Also the linux/amd64 LEG (I7).
    "sha256:2976e77ca1a4e7f5b269b1f001cf870e95728439da45adca416a4a193bcb029a": 2,
    # v0.33.1, rebuilt for the same reason as the digests above: uv.lock is an
    # arena input and the release moves the project's own version in it,
    # 0.33.0 -> 0.33.1. EXACTLY three files differ from the digest above and not
    # one of them is on the scoring path: the website page
    # (src/nethackers/hub/web/index.html -- board copy, served by the hub, never
    # imported by an episode), pyproject.toml, and that single uv.lock line. No
    # package version moves, so the installed environment is identical. arena/
    # and contracts/ are untouched (`git diff v0.33.0...HEAD --
    # src/nethackers/arena src/nethackers/contracts` is empty), and so are
    # nle-base/Dockerfile and arena/Dockerfile. Also the linux/amd64 LEG, not an
    # index -- checked with scripts/check_one_manifest.sh -- so the reference
    # architecture is preserved (I7). Read from the diff, NOT measured: the
    # three digests above were re-run against a knight program because each
    # carried real host-side code changes; this one carries none, so there is no
    # code path whose behaviour a re-run could disconfirm. Same shape as the
    # v0.31.1 entry, which was also read from the diff.
    "sha256:43c22a631d8a9cab72083743b37516d06938edecaba525cd425e8ee01d05043a": 2,
    # v0.33.0, rebuilt for the same reason as the three digests above: uv.lock is
    # an arena input and the release moves the project's own version in it,
    # 0.32.2 -> 0.33.0. Everything else that differs is host-side publishing --
    # hubclient/publish.py (the storage repo's README and About fields, all `gh`
    # calls) and one kwarg in cli.py's `submit` -- which no episode loads.
    # arena/ and contracts/ are untouched (`git diff v0.32.2...HEAD --
    # src/nethackers/arena src/nethackers/contracts` is empty). MEASURED: a
    # registered knight program on the 15 published kni-hum-law-fem seeds matches
    # its record seed for seed in progress, turns, milestone and status, and
    # matches a same-day run under the digest above in depth and cause of death
    # as well. Its agent-step counter is off by one on a single seed (12); two
    # runs of the digest ABOVE differ from each other by one step on seeds 5 and
    # 12, so that is the counter's run-to-run noise, not this rebuild. Also the
    # linux/amd64 LEG (I7).
    "sha256:76dea5e0da80a4fb9f05706fa3bfdf43c72925f8e892f80f28fe032502902f60": 2,
    # v0.34.0 (secure untrusted code + OpenCode 1.x). The FIRST rebuild that
    # touches the arena SCORING PATH itself, not just uv.lock/version -- three
    # files under the image differ from the digest above: arena/run.py,
    # arena/result_io.py (new) and contracts/models.py. SCORES DO NOT MOVE, and
    # this is a STRUCTURAL proof, not a measurement:
    #   - run.py is a pure refactor: run_batch's body from the spec onward was
    #     lifted verbatim into run_prepared (identical Objective(...), identical
    #     run_trajectory, identical fan-out). run_batch now derives the spec then
    #     calls run_prepared; a new stdin path feeds host-pre-derived specs into
    #     that SAME run_prepared. For any (seed, character) the spec -> objective
    #     -> episode -> score pipeline is byte-identical; only the spec's INPUT
    #     SOURCE changed (stdin vs in-container secret derivation, threat 3/INV3).
    #   - the round-trip is lossless: TrajectorySpec.to_dict()=asdict and
    #     from_dict()=cls(**value) over int-seed/str fields, so the reconstructed
    #     spec equals the one --batch derives from the same seed.
    #   - result_io.py only refuses a symlink/oversize/non-list results file --
    #     output a legitimate bot never writes; a normal results.json reads the
    #     same. nle-base/Dockerfile and arena/Dockerfile are unchanged; uv.lock is
    #     the version-only 0.33.1 -> 0.34.0 line. The linux/amd64 LEG (I7). Read
    #     from the diff: the pipeline is provably identical, so no re-run could
    #     disconfirm it.
    "sha256:3bf9731637eb647ac07baaa6b59087012151a568b0ca2a8589ad1e086b0767a7": 2,
    # v0.34.1, rebuilt for the same reason as the v0.33.x digests above: uv.lock
    # is an arena input and the release moves the project's own version in it,
    # 0.34.0 -> 0.34.1. Everything else that differs is host-side login UX --
    # cli.py's device-flow prompt, the TUI's LoginModal and a new browser.py
    # (best-effort "open this URL") -- none of which the arena's entrypoint
    # (`python -m nethackers.arena.run`) imports. arena/ and contracts/ are
    # untouched (`git diff v0.34.0...HEAD -- src/nethackers/arena
    # src/nethackers/contracts` is empty), and so are nle-base/Dockerfile and
    # arena/Dockerfile. No package version moves, so the installed environment
    # is identical. Also the linux/amd64 LEG, not an index -- checked with
    # scripts/check_one_manifest.sh (I7). Read from the diff, NOT measured: no
    # scoring-path code changed, so a re-run has nothing to disconfirm. Same
    # shape as the v0.33.1 entry.
    "sha256:ddbe03a69604a6069a7436583c221e5f81fff2a668540474756f3968fc61d517": 2,
    # v0.34.2, rebuilt for the same reason as the v0.34.1 digest above: uv.lock
    # is an arena input and the release moves the project's own version in it,
    # 0.34.1 -> 0.34.2. Everything else that differs is host-side eval sizing --
    # eval/runner.py, containers.py and sandbox_flags.py (the `docker run`
    # flags the host wraps around this image), plus cli.py, harness/ and
    # worker/ passing an unset --max-parallel-evals through -- none of which
    # the arena's entrypoint (`python -m nethackers.arena.run`) imports.
    # arena/ and contracts/ are untouched (`git diff v0.34.1...HEAD --
    # src/nethackers/arena src/nethackers/contracts` is empty), and so are
    # nle-base/Dockerfile and arena/Dockerfile. No package version moves, so
    # the installed environment is identical. Also the linux/amd64 LEG, not an
    # index -- checked with scripts/check_one_manifest.sh (I7). Read from the
    # diff, NOT measured: no scoring-path code changed, so a re-run has nothing
    # to disconfirm. (The new host flags are not scoring either: on the v0.34.0
    # arena, episodes played under the old and the new caps came out
    # identical.) Same shape as the v0.34.1 entry.
    "sha256:fd68b5ef42f07c1448467ec31348b4682f8b71b6d7a9dee83242653c3374e40c": 2,
    # v0.34.3, rebuilt because uv.lock is an arena input and this release
    # changes it twice: the project's own version (0.34.2 -> 0.34.3) and one
    # added package, truststore 0.10.4. That package is the only change to the
    # installed environment -- every other package keeps its version -- and it
    # is inert here: pure Python, no .pth startup hook, imported only by
    # cli.main to verify host-side HTTPS against the OS trust store. The
    # arena's entrypoint (`python -m nethackers.arena.run`) loads neither
    # nethackers.cli nor truststore (checked: both absent from sys.modules
    # after importing it). The rest of the diff is docs. arena/ and contracts/
    # are untouched (`git diff v0.34.2...HEAD -- src/nethackers/arena
    # src/nethackers/contracts` is empty), and so are nle-base/Dockerfile and
    # arena/Dockerfile. Also the linux/amd64 LEG, not an index -- checked with
    # scripts/check_one_manifest.sh (I7). Read from the diff, NOT measured: no
    # scoring-path code changed and the new package never loads, so a re-run
    # has nothing to disconfirm. Same shape as the v0.34.2 entry, plus that
    # one package.
    "sha256:6059f96ce188915430499d498dbbeacadea83613555c11f819d16f7477796fbe": 2,
    # v0.34.4, rebuilt for the same reason as the v0.34.2 digest above: uv.lock
    # is an arena input and the release moves the project's own version in it,
    # 0.34.3 -> 0.34.4. That line is the whole uv.lock change, so no package
    # version moves and the installed environment is identical. The one code
    # change is hubclient/publish.py, the host-side `git push` of a win to the
    # owner's repo, which the arena's entrypoint (`python -m
    # nethackers.arena.run`) never loads (checked: no nethackers.hubclient
    # module in sys.modules after importing it). arena/ and contracts/ are
    # untouched (`git diff v0.34.3...HEAD -- src/nethackers/arena
    # src/nethackers/contracts` is empty), and so are nle-base/Dockerfile and
    # arena/Dockerfile. Also the linux/amd64 LEG, not an index -- checked with
    # scripts/check_one_manifest.sh (I7). Read from the diff, NOT measured: no
    # scoring-path code changed, so a re-run has nothing to disconfirm. Same
    # shape as the v0.34.2 entry.
    "sha256:63dad174666973067ee8e1c407e30ed8b03287a4e184464ea9aec2a6bb143f18": 2,
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
