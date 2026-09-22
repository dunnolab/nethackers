"""Unit tests for the typed pull-progress event seam
(``harness/pull_events.py`` -- spec S5.5): ``parse_pull_line``'s per-line
reduction over a ``docker pull`` transcript, and ``render_cli_line``'s
compact rendering. No real docker/subprocess involved here -- the raw
transcripts below are plain strings, either a synthetic one built from
docker's documented classic-engine status vocabulary (the parser's ground
truth), or captured verbatim from a real ``docker pull`` run (see each
fixture's own docstring for exactly how).
"""
from __future__ import annotations

import re

from nethackers.harness.pull_events import (
    PullEvent,
    PullParseState,
    parse_pull_line,
    render_cli_line,
)

# ============================================================================
# parse_pull_line -- unit-level behavior on individual lines
# ============================================================================


def test_layers_total_grows_on_each_new_layer_id_seen():
    state = PullParseState()
    state, ev1 = parse_pull_line(state, "1b930d010525: Pulling fs layer", kind="arena", ref="img")
    assert ev1 is not None and ev1.layers_total == 1
    state, ev2 = parse_pull_line(state, "2c841e121636: Pulling fs layer", kind="arena", ref="img")
    assert ev2 is not None and ev2.layers_total == 2
    # A repeat status line for an already-seen id does not grow the total again.
    state, ev3 = parse_pull_line(state, "1b930d010525: Waiting", kind="arena", ref="img")
    assert ev3 is not None and ev3.layers_total == 2


def test_layers_complete_grows_on_pull_complete_and_already_exists():
    state = PullParseState()
    state, _ = parse_pull_line(state, "1b930d010525: Pulling fs layer", kind="arena", ref="img")
    state, _ = parse_pull_line(state, "2c841e121636: Pulling fs layer", kind="arena", ref="img")
    state, ev1 = parse_pull_line(state, "1b930d010525: Already exists", kind="arena", ref="img")
    assert ev1 is not None and ev1.layers_complete == 1
    state, ev2 = parse_pull_line(state, "2c841e121636: Pull complete", kind="arena", ref="img")
    assert ev2 is not None and ev2.layers_complete == 2


def test_downloading_and_extracting_count_as_seen_but_not_complete():
    state = PullParseState()
    state, _ = parse_pull_line(state, "1b930d010525: Pulling fs layer", kind="arena", ref="img")
    state, ev = parse_pull_line(
        state, "1b930d010525: Downloading [==>       ]  1.2MB/23.99MB", kind="arena", ref="img"
    )
    assert ev is not None and ev.phase == "layer"
    assert ev.layers_total == 1 and ev.layers_complete == 0
    state, ev2 = parse_pull_line(state, "1b930d010525: Extracting [====>   ]", kind="arena",
                                 ref="img")
    assert ev2 is not None and ev2.layers_complete == 0


def test_terminal_status_downloaded_line_is_treated_as_noise():
    # Fix round 1: Status: used to trigger its own "done" here, which meant a
    # successful pull double-fired "done" (once from this line, once from
    # _pull_image's own post-loop bracket). Now it's noise, same as Digest:
    # -- _pull_image's final event (built from this function's own final
    # state) is the SOLE authoritative "done" (see test_sandbox_preflight.py:
    # test_pull_image_emits_typed_events_and_still_calls_on_line).
    state = PullParseState()
    state, _ = parse_pull_line(state, "1b930d010525: Pull complete", kind="arena", ref="img:tag")
    state, ev = parse_pull_line(
        state, "Status: Downloaded newer image for img:tag", kind="arena", ref="img:tag"
    )
    assert ev is None


def test_terminal_status_up_to_date_is_treated_as_noise():
    state = PullParseState()
    _, ev = parse_pull_line(
        state, "Status: Image is up to date for img:tag", kind="mutator", ref="img:tag"
    )
    assert ev is None


def test_noise_lines_yield_none():
    state = PullParseState()
    noise_lines = [
        "Using default tag: latest",
        "latest: Pulling from library/example",
        "Digest: sha256:" + "1" * 64,
        "Status: Downloaded newer image for example:latest",
        "Status: Image is up to date for example:latest",
        "docker.io/library/example:latest",
        "",
    ]
    for line in noise_lines:
        _, event = parse_pull_line(state, line, kind="arena", ref="img")
        assert event is None, f"expected noise, got an event for: {line!r}"


def test_parse_pull_line_does_not_mutate_the_passed_in_state():
    state = PullParseState()
    new_state, _ = parse_pull_line(state, "1b930d010525: Pulling fs layer", kind="arena",
                                   ref="img")
    assert state.seen == frozenset()  # the original is untouched
    assert new_state.seen == frozenset({"1b930d010525"})


# ============================================================================
# A full synthetic transcript, driven end to end -- the ground-truth status
# vocabulary given in the task brief (docker's classic, non-containerd-
# snapshotter engine): 3 layers, one already cached, two downloaded through
# every intermediate status, ending in the one-line terminal marker.
# ============================================================================

_SYNTHETIC_TRANSCRIPT = [
    "Using default tag: latest",
    "latest: Pulling from library/example",
    "1b930d010525: Pulling fs layer",
    "2c841e121636: Pulling fs layer",
    "3da52f232747: Pulling fs layer",
    "3da52f232747: Already exists",
    "1b930d010525: Waiting",
    "2c841e121636: Waiting",
    "2c841e121636: Verifying Checksum",
    "2c841e121636: Download complete",
    "1b930d010525: Downloading [>                            ]  539.6kB/23.99MB",
    "1b930d010525: Downloading [====================>       ]   23.9MB/23.99MB",
    "1b930d010525: Verifying Checksum",
    "1b930d010525: Download complete",
    "2c841e121636: Extracting [===================>        ]  1.024kB/1.024kB",
    "2c841e121636: Pull complete",
    "1b930d010525: Extracting [===================>        ]  23.99MB/23.99MB",
    "1b930d010525: Pull complete",
    "Digest: sha256:" + "9" * 64,
    "Status: Downloaded newer image for example:latest",
    "docker.io/library/example:latest",
]


def test_synthetic_transcript_final_layer_tally_and_no_parser_done():
    state = PullParseState()
    events: list[PullEvent] = []
    for line in _SYNTHETIC_TRANSCRIPT:
        state, event = parse_pull_line(state, line, kind="arena", ref="example:latest")
        if event is not None:
            events.append(event)

    layer_events = [e for e in events if e.phase == "layer"]
    assert layer_events, "expected at least one layer event"
    assert layer_events[-1].layers_total == 3
    assert layer_events[-1].layers_complete == 3
    # layers_total/complete are monotonically non-decreasing throughout.
    totals = [e.layers_total for e in layer_events]
    completes = [e.layers_complete for e in layer_events]
    assert totals == sorted(totals)
    assert completes == sorted(completes)

    # The terminal Status: line is noise at the parser level (fix round 1) --
    # parse_pull_line alone never produces "done"; only _pull_image's
    # post-loop bracket does (covered in test_sandbox_preflight.py).
    assert [e for e in events if e.phase == "done"] == []


# ============================================================================
# Real `docker pull` transcripts, captured 2026-08-30 (see task-4-report.md
# for the exact commands). This sandbox's docker daemon reports `driver-type:
# io.containerd.snapshotter.v1` in `docker info` -- a storage driver that, it
# turns out, abbreviates per-layer reporting versus the classic engine the
# vocabulary above is grounded in: it never emits "Waiting" / "Verifying
# Checksum" / "Extracting" / "Pull complete" for a freshly-downloaded layer,
# only "Pulling fs layer" then "Download complete". Kept as real-world
# regressions: the parser must handle them without crashing, and (fix round
# 1) the terminal Status: line is noise here just like everywhere else --
# "did it finish" is answered by _pull_image's own post-loop done/error
# event, not by anything parse_pull_line itself produces (see
# sandbox_preflight._final_pull_event and its dedicated tests in
# test_sandbox_preflight.py).
# ============================================================================

_REAL_HELLO_WORLD_FRESH_PULL = [
    "Using default tag: latest",
    "latest: Pulling from library/hello-world",
    "58dee6a49ef1: Pulling fs layer",
    "58dee6a49ef1: Download complete",
    "Digest: sha256:5dd0d3e6e255913fc30f90b9f2b1d359cc2cbdb48090cc4b65f1676e203243cc",
    "Status: Downloaded newer image for hello-world:latest",
    "docker.io/library/hello-world:latest",
]

_REAL_ALPINE_ALREADY_CURRENT = [
    "3.19: Pulling from library/alpine",
    "Digest: sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1",
    "Status: Image is up to date for alpine:3.19",
    "docker.io/library/alpine:3.19",
]


def test_real_containerd_snapshotter_fresh_pull_layer_tally_only():
    state = PullParseState()
    events: list[PullEvent] = []
    for line in _REAL_HELLO_WORLD_FRESH_PULL:
        state, event = parse_pull_line(state, line, kind="arena", ref="hello-world:latest")
        if event is not None:
            events.append(event)
    # No "done" from the parser (fix round 1) -- every event here is "layer".
    assert events and all(e.phase == "layer" for e in events)
    assert events[-1].layers_total == 1  # the one "Pulling fs layer" line
    # This driver never prints "Pull complete" -- documented under-report,
    # not a crash or a wrong phase.
    assert events[-1].layers_complete == 0


def test_real_already_current_pull_yields_no_parser_events_at_all():
    # No layer lines AND the terminal Status: line is now noise too (fix
    # round 1) -- a fully-cached pull produces literally nothing from the
    # parser. _pull_image's own post-loop "done" bracket still fires exactly
    # once regardless (tested in test_sandbox_preflight.py).
    state = PullParseState()
    events: list[PullEvent] = []
    for line in _REAL_ALPINE_ALREADY_CURRENT:
        state, event = parse_pull_line(state, line, kind="arena", ref="alpine:3.19")
        if event is not None:
            events.append(event)
    assert events == []


# ============================================================================
# render_cli_line
# ============================================================================


def test_render_cli_line_layer_phase_matches_the_spec_example():
    ev = PullEvent(kind="arena", ref="nethackers/arena:dev", phase="layer",
                   layers_total=12, layers_complete=7, detail="Pull complete")
    assert render_cli_line(ev) == "pulling arena  7/12 layers"


def test_render_cli_line_never_shows_a_raw_64_hex_digest():
    ref = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64
    for phase in ("start", "done", "error"):
        ev = PullEvent(kind="arena", ref=ref, phase=phase, layers_total=None,
                       layers_complete=None, detail="")
        line = render_cli_line(ev)
        assert re.search(r"[0-9a-f]{64}", line) is None, line
        assert "arena" in line


def test_render_cli_line_start_done_error_include_kind_and_ref():
    ref = "nethackers/arena:dev"
    start = render_cli_line(PullEvent(kind="arena", ref=ref, phase="start", layers_total=None,
                                      layers_complete=None, detail=""))
    done = render_cli_line(PullEvent(kind="arena", ref=ref, phase="done", layers_total=3,
                                     layers_complete=3, detail=""))
    error = render_cli_line(PullEvent(kind="mutator", ref=ref, phase="error", layers_total=None,
                                      layers_complete=None, detail="boom"))
    assert "arena" in start and ref in start
    assert "arena" in done and ref in done
    assert "mutator" in error and ref in error


# ============================================================================
# byte progress (pulls run in a pseudo-terminal, so docker prints its bytes)
# ============================================================================


def _feed(lines):
    state, events = PullParseState(), []
    for line in lines:
        state, event = parse_pull_line(state, line, kind="mutator", ref="img")
        if event is not None:
            events.append(event)
    return state, events


def test_downloading_lines_carry_bytes():
    _, events = _feed(["0a1b2c3d4e5f: Downloading [==>      ]  45.21MB/355.2MB",
                       "1b2c3d4e5f60: Downloading [=====>   ]  1.2MB/2.5MB"])
    assert events[-1].bytes_done == 45_210_000 + 1_200_000
    assert events[-1].bytes_total == 355_200_000 + 2_500_000


def test_a_finished_download_counts_its_whole_size():
    _, events = _feed(["0a1b2c3d4e5f: Downloading [==>      ]  45.21MB/355.2MB",
                       "0a1b2c3d4e5f: Download complete"])
    assert events[-1].bytes_done == events[-1].bytes_total == 355_200_000


def test_extracting_means_the_download_is_done():
    _, events = _feed(["0a1b2c3d4e5f: Downloading [==>      ]  1MB/2MB",
                       "0a1b2c3d4e5f: Extracting [=>        ]  512kB/2MB"])
    assert events[-1].bytes_done == 2_000_000


def test_a_layer_that_already_exists_is_not_part_of_the_download():
    _, events = _feed(["0a1b2c3d4e5f: Downloading [==>      ]  1MB/2MB",
                       "1b2c3d4e5f60: Already exists"])
    assert events[-1].bytes_total == 2_000_000


def test_no_byte_lines_means_no_bytes():
    _, events = _feed(["1b930d010525: Pulling fs layer", "1b930d010525: Pull complete"])
    assert events[-1].bytes_done is None and events[-1].bytes_total is None


def test_render_cli_line_shows_megabytes_once_known():
    event = PullEvent(kind="mutator", ref="img", phase="layer", layers_total=19,
                      layers_complete=5, detail="Downloading", bytes_done=212_000_000,
                      bytes_total=432_000_000)
    assert render_cli_line(event) == "pulling mutator  212/432 MB"
