"""Wording tests for tui.story -- the evolve monitor's plain-language narration.
Built on Runs driven through their real apply_* reductions with a fake clock,
so every duration is exact."""
from __future__ import annotations

import json
import subprocess

from rich.text import Text

from nethackers.harness.loop import IterationResult
from nethackers.harness.metering import TokenUsage
from nethackers.tui import story
from nethackers.tui.run import Run
from nethackers.tui.status import EvolveConfig

IDS = ["val-dwa-law-fem", "val-hum-neu-fem", "wiz-elf-cha-mal"]
CLYDE, MOSS = "aaaa1111bbbb", "cccc2222dddd"
ORIGINS = {
    CLYDE: {"kind": "hub", "handle": "clyde", "sha": "a1b2c3d",
            "repo": "github.com/clyde/nh", "iteration": None},
    MOSS: {"kind": "hub", "handle": "moss", "sha": "9f8e7d6",
           "repo": "github.com/moss/nh", "iteration": None},
}
ELITE_OF = {IDS[0]: {"program_id": CLYDE, "score": 0.22},
            IDS[1]: {"program_id": CLYDE, "score": 0.19},
            IDS[2]: {"program_id": MOSS, "score": 0.13}}
CELLS = [{"identity": IDS[0], "score": 0.21, "digest": CLYDE},
         {"identity": IDS[1], "score": 0.18, "digest": CLYDE},
         {"identity": IDS[2], "score": 0.12, "digest": MOSS}]


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def plain(markup: str | None) -> str:
    return Text.from_markup(markup or "").plain


def _state(phase: str, iteration: int = 0, **kw) -> dict:
    s = {"phase": phase, "iteration": iteration, "identities": IDS, "cells": [],
         "cell_results": {}, "union": None, "coverage": (0, 3), "cell": None,
         "generation": iteration, "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0,
         "tokens": 0, "detail": "", "parent_digest": "", "parent_dev": 0.0,
         "aa_baseline": {i: 0.07 for i in IDS}, "elite_of": ELITE_OF, "origins": ORIGINS}
    s.update(kw)
    return s


def _ep(run: Run, label: str, i: int, total: int, ident: str, progress: float) -> None:
    run.apply_episode(label, {"index": i, "total": total, "seed": i, "progress": progress,
                              "status": "completed", "character": ident})


def _new(clock: Clock, backend: str = "codex", **cfg) -> Run:
    return Run("r1", EvolveConfig(",".join(IDS), backend, 5, **cfg), clock=clock)


def _through_setup(clock: Clock) -> Run:
    """Setup: hub answered at 1006, clyde played 1006->1400, moss 1400->1490;
    iteration 1 (improving val-hum-neu-fem) started at 1500."""
    run = _new(clock)
    clock.t = 1006
    run.apply_state(_state("cold-start"))
    clock.t = 1400
    for i in range(30):
        _ep(run, "cold-start · dev [aaaa1111]", i, 30, IDS[i % 2], 0.2)
    clock.t = 1490
    for i in range(15):
        _ep(run, "cold-start · dev [cccc2222]", i, 15, IDS[2], 0.12)
    run.apply_state(_state("cold-start", cells=CELLS, coverage=(3, 3)))
    clock.t = 1500
    run.apply_state(_state("mutating", 1, cells=CELLS, cell=IDS[1]))
    return run


def _codex_edit(path: str) -> str:
    return json.dumps({"type": "item.started",
                       "item": {"type": "file_change", "changes": [{"path": path}]}})


def test_the_now_line_says_it_is_fetching_before_the_first_state():
    clock = Clock()
    run = _new(clock)
    clock.t = 1004
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Fetching the best bots for your 3 identities from the hub… · 4s")


def test_from_seed_does_not_claim_a_hub_fetch():
    clock = Clock()
    run = _new(clock, from_seed=True)
    assert "Preparing the starting bot for your 3 identities" in plain(
        story.now_line(run, clock.t))


def test_setup_now_line_and_steps_while_a_champion_plays():
    clock = Clock()
    run = _new(clock)
    clock.t = 1006
    run.apply_state(_state("cold-start"))
    _ep(run, "cold-start · dev [aaaa1111]", 0, 30, IDS[0], 0.2)
    clock.t = 1100
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Setup · playing the hub's best bots on your machine for a fair starting score"
        " · 1/30 games · 1m 34s")
    view = story.section_view(run, 0, clock.t)
    assert [plain(r.label) for r in view.rows] == [
        "fetched the best bots from the hub · 2 bots cover your 3 identities",
        "playing clyde @a1b2c3d on val-dwa-law-fem, val-hum-neu-fem · 1/30 games · avg 0.20",
        "play moss @9f8e7d6 on wiz-elf-cha-mal · 15 games",
    ]
    assert [plain(r.mark) for r in view.rows] == ["✓", "▶", "·"]
    assert (view.rows[0].dur, view.rows[1].dur) == ("6s", "1m 34s")
    assert "next: 5 iterations" in plain(view.footer)


def test_the_setup_view_after_setup_says_how_long_it_took():
    clock = Clock()
    run = _through_setup(clock)
    view = story.section_view(run, 0, clock.t)
    assert [plain(r.mark) for r in view.rows] == ["✓", "✓", "✓"]
    assert (view.rows[1].dur, view.rows[2].dur) == ("6m 34s", "1m 30s")
    assert plain(view.footer).startswith("✓ setup done in 8m 20s")


def test_the_iteration_now_line_counts_the_agents_actions():
    clock = Clock()
    run = _through_setup(clock)
    for p in ("/w/src/combat.py", "/w/src/prayer.py", "/w/bot.py"):
        run.apply_log(run.tag(1), _codex_edit(p))
    clock.t = 1590
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Iteration 1 of 5 · Codex is editing the bot for val-hum-neu-fem · 3 actions · 1m 30s")
    view = story.section_view(run, 1, clock.t)
    assert plain(view.header) == (
        "Iteration 1 of 5 — improving val-hum-neu-fem (best so far 0.18 · clyde @a1b2c3d)")
    assert plain(view.rows[0].label) == "Codex is editing the bot · 3 actions so far"
    assert plain(view.rows[1].label) == "last: edit bot.py — full transcript in Mutator Logs"
    assert [plain(r.mark) for r in view.rows[2:]] == ["·", "·", "·"]
    assert plain(view.rows[4].label) == (
        "decide: keep it if it beats the best so far on any identity, or the best average")


def test_the_iteration_view_from_edit_to_decision():
    clock = Clock()
    run = _through_setup(clock)
    run.apply_log(run.tag(1), _codex_edit("/w/bot.py"))
    run.apply_log(run.tag(1), json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 900_000, "cached_input_tokens": 100_000, "output_tokens": 41_000}}))
    clock.t = 1872
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1890
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1950
    _ep(run, "iter 1/5 · dev", 0, 45, IDS[1], 0.25)
    view = story.section_view(run, 1, clock.t)
    labels = [plain(r.label) for r in view.rows]
    assert labels[0] == "Codex edited the bot · 1 action · 841.0k tokens"
    assert view.rows[0].dur == "6m 12s"
    assert (labels[1], view.rows[1].dur) == ("smoke test passed", "18s")
    assert labels[2] == "playing the edited bot · 1/45 games · avg 0.25"
    assert labels[3].startswith("decide:")
    run.apply_iteration(1, IterationResult(
        True, "registered", dev_fitness=0.16, improved=[IDS[1]],
        results=[{"character": IDS[1], "progress": 0.23},
                 {"character": IDS[0], "progress": 0.15}]))
    clock.t = 2080
    run.apply_state(_state("registered", 1, cells=CELLS, cell=IDS[1]))
    labels = [plain(r.label) for r in story.section_view(run, 1, clock.t).rows]
    assert labels[2] == "played the edited bot · 45 games · avg 0.16"
    assert labels[3] == "improved val-hum-neu-fem 0.23 (was 0.18)"
    assert labels[4] == "sent to the hub"


def test_a_failed_smoke_test_ends_the_list_with_its_reason():
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1560
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    run.apply_iteration(1, IterationResult(False, "gate:smoke episode crashed"))
    clock.t = 1600
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1],
                           detail="gate: smoke episode crashed"))
    view = story.section_view(run, 1, clock.t)
    assert plain(view.rows[-2].label) == "smoke test failed — smoke episode crashed"
    assert plain(view.rows[-1].label) == "not played, not sent to the hub"
    label, disabled = story.iter_label(run, 1)
    assert plain(label) == "✗ iter 1   failed test  1m" and disabled is False


def test_no_gain_and_a_bot_that_stayed_local():
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1700
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    run.apply_iteration(1, IterationResult(
        False, "no-cell-improved", dev_fitness=0.12, results=[],
        hub_reason="local-only: hub error — 503 Service Unavailable"))
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1]))
    rows = story.section_view(run, 1, clock.t).rows
    assert plain(rows[-2].label) == (
        "no gain — it didn't beat the best so far on any identity, or the best average")
    assert plain(rows[-1].label) == "stayed local: hub error — 503 Service Unavailable"
    assert plain(rows[-1].mark) == "⚠"


def test_pace_joins_the_now_line_after_the_first_finished_iteration():
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1800
    run.apply_iteration(1, IterationResult(False, "no-cell-improved", dev_fitness=0.1))
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1810
    run.apply_state(_state("mutating", 2, cells=CELLS, cell=IDS[0]))
    clock.t = 1870
    # 300 s per iteration x 3 after this one + (300 - 60) left of it = 1140 s
    assert plain(story.now_line(run, clock.t)).endswith("· at this pace ~19m left")


def test_stopping_says_what_still_runs():
    clock = Clock()
    run = _through_setup(clock)
    run.request_stop()
    assert plain(story.now_line(run, clock.t)).startswith(
        "■ Stopping · the agent is stopped; this iteration's smoke test and games still run")
    setup = _new(Clock())
    setup.apply_state(_state("cold-start"))
    setup.request_stop()
    assert plain(story.now_line(setup, setup.now())).startswith(
        "■ Stopping once setup finishes")


def test_the_final_now_lines():
    clock = Clock()
    done = _through_setup(clock)
    done.finish(results=[])
    assert plain(story.now_line(done, clock.t)) == "✓ Done · 5 iterations · 0 improved · 8m"
    stopped = _through_setup(Clock())
    stopped.request_stop()
    stopped.finish()
    assert plain(story.now_line(stopped, 0)).startswith("■ Stopped by you · 0 of 5 improved")
    failed = _through_setup(Clock())
    failed.finish(error=RuntimeError("docker: cannot connect"))
    assert plain(story.now_line(failed, 0)) == "✗ Run failed: docker: cannot connect"


def test_three_agent_failures_in_a_row_are_named():
    clock = Clock()
    run = _through_setup(clock)
    for k in (1, 2, 3):
        run.apply_iteration(k, IterationResult(False, "operator-error:exit 1"))
    run.finish(results=[])
    assert plain(story.now_line(run, clock.t)) == (
        "✗ Stopped after 3 failed agent runs in a row · last: exit 1")


def test_iteration_list_labels():
    clock = Clock()
    run = _new(clock)
    run.apply_state(_state("cold-start"))
    assert plain(story.iter_label(run, 0)[0]) == "▶ setup   live"
    run = _through_setup(clock)
    assert plain(story.iter_label(run, 0)[0]) == "✓ setup   8m"
    assert plain(story.iter_label(run, 1)[0]) == "▶ iter 1   live"
    assert story.iter_label(run, 3)[1] is True   # pending
    assert plain(story.iter_label(run, 3)[0]) == "·  iter 3"
    clock.t = 2000
    run.apply_iteration(1, IterationResult(True, "registered", improved=[IDS[1]]))
    run.apply_state(_state("registered", 1, cells=CELLS, cell=IDS[1]))
    assert plain(story.iter_label(run, 1)[0]) == "✓ iter 1   improved  8m"


def test_this_iteration_cell_says_what_it_waits_on():
    clock = Clock()
    run = _through_setup(clock)
    assert plain(story.this_cell(run, IDS[1], 0, 0.18)[0]) == "— setup doesn't edit"
    assert plain(story.this_cell(run, IDS[1], 1, 0.18)[0]) == "waiting for the agent…"
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    assert plain(story.this_cell(run, IDS[1], 1, 0.18)[0]) == "smoke test…"
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    _ep(run, "iter 1/5 · dev", 0, 45, IDS[1], 0.25)
    text, clickable = story.this_cell(run, IDS[1], 1, 0.18)
    assert plain(text) == "0.25  1/15 games  ▲ new best" and clickable is True
    assert plain(story.this_cell(run, None, 1, 0.3)[0]) == "0.25  1/45 games"


def test_reopened_runs_say_so_and_skip_timings():
    run = Run("r", EvolveConfig(",".join(IDS), "claude", 5))
    run.reopened = True
    run.status = "done"
    run.apply_iteration(1, IterationResult(True, "registered", improved=[IDS[0]],
                                           dev_fitness=0.2, usage=TokenUsage(1000, 100, 0, 0)))
    assert plain(story.now_line(run, 0)) == (
        "✓ Reopened from an earlier session · 1 of 5 improved")
    view = story.section_view(run, 1, 0)
    labels = [plain(r.label) for r in view.rows]
    assert labels[0] == "Claude Code edited the bot · 0 actions · 1.1k tokens"
    assert "improved val-dwa-law-fem" in labels
    assert "weren't recorded" in plain(view.footer)
    assert "wasn't recorded" in plain(story.section_view(run, 0, 0).footer)


def test_reopened_iteration_shows_only_the_section_6_8_rows():
    """Ruling 10 minor: a reopened run has no timestamps at all, so it must
    never add a smoke-passed or played-games row -- only §6.8's three:
    edit-done, outcome, hub."""
    run = Run("r", EvolveConfig(",".join(IDS), "claude", 5))
    run.reopened = True
    run.status = "done"
    run.apply_iteration(1, IterationResult(True, "registered", improved=[IDS[0]],
                                           dev_fitness=0.2, usage=TokenUsage(1000, 100, 0, 0)))
    view = story.section_view(run, 1, 0)
    assert [plain(r.mark) for r in view.rows] == ["✓", "✓", "✓"]
    labels = [plain(r.label) for r in view.rows]
    assert labels == ["Claude Code edited the bot · 0 actions · 1.1k tokens",
                      "improved val-dwa-law-fem", "sent to the hub"]


def test_a_reopened_error_iteration_shows_only_the_error_line():
    """Round-1 leftover (finding 1, still open in round 2): the `not
    run.reopened` exemption in _edit_rows let a reopened error: result fall
    through to a false "edited the bot" row, since a reopened run has no
    edit_end either way. Ruling 6: with no timings at all, a reopened
    error: result shows ONLY the error line -- no edit row at all."""
    run = Run("r", EvolveConfig(",".join(IDS), "claude", 5))
    run.reopened = True
    run.status = "done"
    run.apply_iteration(1, IterationResult(False, "error:copytree failed"))
    view = story.section_view(run, 1, 0)
    assert [plain(r.label) for r in view.rows] == ["the iteration hit an error: copytree failed"]
    assert [plain(r.mark) for r in view.rows] == ["✗"]


# ---- fix round 1 (task-6-findings-r1.md) -----------------------------------------

def test_an_error_iteration_claims_only_the_steps_that_really_happened():
    """Finding 1 / Ruling 6: the loop's outer except can land anywhere from
    before "mutating" through the dev eval. Two sub-cases: (a) it happens
    after the edit really finished (we reached "gating") but during the
    smoke test -- claim the edit, not the smoke test; (b) it happens before
    the agent ever ran (e.g. a copytree failure) -- claim nothing at all
    except the error itself."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))   # the edit really finished
    clock.t = 1610
    run.apply_state(_state("error", 1, cells=CELLS, detail="docker: daemon gone"))
    run.apply_iteration(1, IterationResult(False, "error:docker: daemon gone"))
    view = story.section_view(run, 1, 1610)
    labels = [plain(r.label) for r in view.rows]
    assert labels == ["Codex edited the bot · 0 actions",
                      "the iteration hit an error: docker: daemon gone"]
    assert [plain(r.mark) for r in view.rows] == ["✓", "✗"]
    assert view.rows[0].dur == "1m 40s"          # edit_start(1500) -> edit_end(1600), real

    # iteration 2 never even reached "mutating" -- no edit row, no smoke row.
    clock.t = 1620
    run.apply_state(_state("error", 2, cells=CELLS, detail="copytree failed"))
    run.apply_iteration(2, IterationResult(False, "error:copytree failed"))
    view2 = story.section_view(run, 2, 1620)
    assert [plain(r.label) for r in view2.rows] == ["the iteration hit an error: copytree failed"]
    assert [plain(r.mark) for r in view2.rows] == ["✗"]
    assert plain(story.iter_label(run, 2)[0]) == "✗ iter 2   error"


def test_a_worker_crash_during_setup_shows_the_partial_count_not_a_checkmark():
    """Finding 2a: Run.finish() force-seals the in-progress cold-start batch
    (batch.done=True) even though only 5 of 30 games arrived -- the row must
    keep saying 5/30, never claim all 30 played."""
    clock = Clock()
    run = _new(clock)
    clock.t = 1006
    run.apply_state(_state("cold-start"))
    for i in range(5):
        _ep(run, "cold-start · dev [aaaa1111]", i, 30, IDS[i % 2], 0.2)
    clock.t = 1100
    run.finish(error=RuntimeError("docker died"))
    view = story.section_view(run, 0, clock.t)
    assert plain(view.rows[1].label) == (
        "playing clyde @a1b2c3d on val-dwa-law-fem, val-hum-neu-fem · 5/30 games · avg 0.20")
    assert plain(view.rows[1].mark) == "✗"


def test_an_iteration_cut_short_by_a_worker_crash_shows_the_partial_count():
    """Finding 2a (the iteration-side twin of the setup case above) and
    finding 2c's this_cell bullet, from the same crash: Run.finish() seals
    the dev batch at 12/45 games -- the row must say 12/45, and this_cell
    must show the real 12/45 too, not 0/45 (Run.iteration_evals's live path
    now reads the iteration's own dev batch directly, regardless of
    Run.running -- Ruling 12). Also (round 2 finding 7): the play row's own
    mark, iter_label, and the section footer must all read as a crash (✗
    failed), never a stop (■) -- a crash was never previously asserted here,
    only the label text and this_cell were."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1650
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    for i in range(12):
        _ep(run, "iter 1/5 · dev", i, 45, IDS[i % 3], 0.15)
    clock.t = 1700
    run.finish(error=RuntimeError("arena crashed"))
    view = story.section_view(run, 1, clock.t)
    labels = [plain(r.label) for r in view.rows]
    assert labels[2] == "playing the edited bot · 12/45 games · avg 0.15"   # never a checkmark
    assert plain(view.rows[2].mark) == "✗"                                  # crash, not a stop
    assert plain(story.this_cell(run, None, 1, 0.3)[0]) == "0.15  12/45 games"
    assert plain(story.iter_label(run, 1)[0]) == "✗ iter 1   failed"
    assert plain(view.footer) == "✗ the run failed during this iteration"


def test_a_crash_mid_edit_marks_the_row_failed_not_stopped():
    """Finding 2c: the edit row's own "still in progress" mark must also
    read as a crash, not a stop, once the run isn't running and the
    iteration is undecided (the re-reviewer's key fact: this combination can
    only mean status == "failed" -- the loop only checks Stop at the top of
    an iteration, so a genuine user Stop never leaves one undecided)."""
    clock = Clock()
    run = _through_setup(clock)
    run.apply_log(run.tag(1), _codex_edit("/w/bot.py"))
    clock.t = 1580
    run.finish(error=RuntimeError("docker daemon died"))
    view = story.section_view(run, 1, clock.t)
    assert plain(view.rows[0].label) == "Codex is editing the bot · 1 action so far"
    assert plain(view.rows[0].mark) == "✗"
    assert plain(story.this_cell(run, IDS[1], 1, 0.18)[0]) == "— failed"


def test_a_crash_mid_smoke_test_marks_the_row_failed_not_stopped():
    """Finding 2c: the smoke-test row's own "still in progress" mark, same
    reasoning as the edit-row test above."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1610
    run.finish(error=RuntimeError("docker daemon died"))
    view = story.section_view(run, 1, clock.t)
    assert plain(view.rows[-1].label) == "smoke test: one short game to check the edited bot runs"
    assert plain(view.rows[-1].mark) == "✗"


def test_a_run_that_fails_before_any_state_does_not_look_live():
    """Finding 2b: the setup view's "fetching…" row must reflect a failure
    that happened before the first state ever arrived -- not keep claiming
    to be running, timed against `now` instead of when it actually died."""
    clock = Clock()
    run = _new(clock)
    clock.t = 1010
    run.finish(error=RuntimeError("hub unreachable"))
    clock.t = 1200
    assert plain(story.now_line(run, clock.t)) == "✗ Run failed: hub unreachable"
    view = story.section_view(run, 0, clock.t)
    assert plain(view.rows[0].mark) == "✗"
    assert view.rows[0].dur == "10s"                 # frozen at finished_at(1010), not now(1200)
    assert plain(view.footer) == "✗ setup didn't finish"
    assert plain(story.iter_label(run, 0)[0]) == "✗ setup   failed"


def test_a_user_stop_mid_iteration_reads_differently_from_a_crash():
    """Finding 2c: the SAME "not running, undecided iteration" shape must be
    worded differently for a user Stop (■ stopped) than for a crash (✗
    failed, tested in test_an_iteration_cut_short_by_a_worker_crash_shows_the_partial_count
    and the two test_a_crash_mid_* tests above) -- a crash must never read as
    an intentional stop and a stop must never read as a failure. Per the
    round-2 re-reviewer's key fact, the loop only checks Stop at the top of
    an iteration, so a REAL Stop never actually leaves one undecided --
    "status == stopped, iteration undecided" is synthetic here (built by
    calling finish() directly, bypassing the loop's own invariant), but the
    wording must still be correct if it's ever reached."""
    clock = Clock()
    run = _through_setup(clock)
    run.request_stop()
    clock.t = 1600
    run.finish()   # no error, but stop was requested -> status "stopped"
    assert plain(story.iter_label(run, 1)[0]) == "■ iter 1   stopped"
    assert plain(story.section_view(run, 1, clock.t).footer) == (
        "■ the run stopped during this iteration")


def test_this_cell_does_not_count_the_smoke_game_as_the_first_dev_game():
    """Finding 3: the smoke game streams under a DIFFERENT label
    ("iter k/n · smoke", on identities[0]) than the dev batch -- this_cell
    must read the iteration's own dev batch, so it shows 0/N until a real
    dev game lands, not the smoke game's score passed off as one."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    run.apply_episode("iter 1/5 · smoke", {"index": 0, "total": 1, "seed": 9000,
                                           "progress": 0.40, "status": "completed",
                                           "character": IDS[0]})
    clock.t = 1650
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    assert plain(story.this_cell(run, IDS[0], 1, 0.21)[0]) == "0/15 games"
    assert plain(story.this_cell(run, IDS[1], 1, 0.21)[0]) == "0/15 games"
    assert plain(story.this_cell(run, None, 1, 0.21)[0]) == "0/45 games"
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Iteration 1 of 5 · playing the edited bot · 0/45 games · 0s")


def test_the_single_identity_keep_rule_uses_a_seed_cells_measured_score():
    """Ruling 7 (finding 4): a --from-seed cell has no hub champion, so its
    origin's kind is "seed", not "hub" -- the keep rule (and "best so far")
    must cite its MEASURED score (0.09, what CellArchive.insert actually
    compares against), not the AutoAscend floor, which --from-seed leaves
    empty (0.0)."""
    clock = Clock()
    ident = IDS[0]
    seed_digest = "5eed5eed5eed"

    def st(phase: str, it: int = 0, **kw) -> dict:
        s = _state(phase, it, identities=[ident], elite_of={}, aa_baseline={},
                   origins={seed_digest: {"kind": "seed", "handle": None, "sha": None,
                                          "repo": None, "iteration": None}},
                   coverage=(1, 1))
        s.update(kw)
        return s

    run = Run("r1", EvolveConfig(ident, "codex", 5, from_seed=True), clock=clock)
    clock.t = 1002
    run.apply_state(st("cold-start"))
    for i in range(15):
        _ep(run, "cold-start · dev [seed]", i, 15, ident, 0.09)
    cells = [{"identity": ident, "score": 0.09, "digest": seed_digest}]
    run.apply_state(st("cold-start", cells=cells))
    clock.t = 1050
    run.apply_state(st("mutating", 1, cells=cells, cell=ident))
    view = story.section_view(run, 1, clock.t)
    assert plain(view.rows[-1].label) == "decide: keep it if its average beats 0.09"
    setup_footer = plain(story.section_view(run, 0, clock.t).footer)
    assert "best so far 0.09 (AutoAscend)" in setup_footer


def test_seed_setup_row_says_no_hub_champion_yet():
    """Finding 5: the seed batch's row must carry the spec's "(no hub
    champion yet)" qualifier -- a real, empty-handed hub search earns it."""
    clock = Clock()
    run = _new(clock)
    clock.t = 1006
    elite = {IDS[0]: {"program_id": CLYDE, "score": 0.22}, IDS[1]: {"program_id": CLYDE,
                                                                    "score": 0.19}}
    run.apply_state(_state("cold-start", elite_of=elite))
    _ep(run, "cold-start · dev [aaaa1111]", 0, 30, IDS[0], 0.2)
    labels = [plain(r.label) for r in story.section_view(run, 0, clock.t).rows]
    assert labels[-1] == "play the starting bot on wiz-elf-cha-mal (no hub champion yet) · 15 games"


def test_seed_setup_row_omits_the_hub_claim_under_from_seed():
    """Finding 5: --from-seed never asks the hub at all (loop.py skips it),
    so claiming "no hub champion" would overstate what happened -- omit it."""
    clock = Clock()
    run = _new(clock, from_seed=True)
    clock.t = 1006
    run.apply_state(_state("cold-start", elite_of={}))
    view = story.section_view(run, 0, clock.t)
    assert plain(view.rows[-1].label) == (
        "playing the starting bot on val-dwa-law-fem, val-hum-neu-fem, wiz-elf-cha-mal"
        " · 0/45 games")


def test_union_setup_row_has_the_specs_double_comma_once_done():
    """Finding 5 + Ruling 9: keep "<label>, the best bot on average" once the
    union batch is done -- only add the comma the spec has before
    "on all N identities" that the code was missing. Round 2 (Ruling 12):
    the comma belongs ONLY after a real label -- the live/unlabelled row
    (before the union champion is named) must carry no stray comma at all,
    matching the now line's own phrasing for the same moment."""
    clock = Clock()
    run = _new(clock)
    clock.t = 1006
    run.apply_state(_state("cold-start"))
    for i in range(30):
        _ep(run, "cold-start · dev [aaaa1111]", i, 30, IDS[i % 2], 0.2)
    for i in range(15):
        _ep(run, "cold-start · dev [cccc2222]", i, 15, IDS[2], 0.12)
    run.apply_state(_state("cold-start", cells=CELLS, coverage=(3, 3)))
    for i in range(10):
        _ep(run, "cold-start · dev [union]", i, 45, IDS[i % 3], 0.16)
    live_labels = [plain(r.label) for r in story.section_view(run, 0, 1300).rows]
    assert live_labels[-1] == (
        "playing the best bot on average on all 3 identities · 10/45 games · avg 0.16")
    for i in range(10, 45):
        _ep(run, "cold-start · dev [union]", i, 45, IDS[i % 3], 0.16)
    run.apply_state(_state("cold-start", cells=CELLS, coverage=(3, 3),
                           union={"score": 0.16, "digest": CLYDE}))
    labels = [plain(r.label) for r in story.section_view(run, 0, 1300).rows]
    assert labels[-1] == ("played clyde @a1b2c3d, the best bot on average, on all 3 identities"
                          " · 45 games · avg 0.16")


def test_run_failed_uses_failure_detail_not_the_raw_exception():
    """Finding 6 + Ruling 8: the now-line reuses tui._util.failure_detail (the
    same helper the app's toast uses) -- a CalledProcessError's own str() is
    an unreadable giant command repr; failure_detail distills docker's own
    stderr line instead."""
    clock = Clock()
    run = _through_setup(clock)
    err = subprocess.CalledProcessError(
        125, ["docker", "run", "--rm", "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64],
        stderr=b"docker: Error response from daemon: No such image.\nSee 'docker run --help'.\n")
    run.finish(error=err)
    assert plain(story.now_line(run, clock.t)) == (
        "✗ Run failed: docker: Error response from daemon: No such image.")


def test_stopping_between_iterations_is_acknowledged():
    """Ruling 10 minor, revised by Ruling 12: Stop pressed between iterations
    (none currently running) must say so at once -- but must claim nothing
    about whether another iteration starts, since Stop landing right after
    the loop's top-of-iteration check still lets the next one begin (its
    agent killed at once, but its smoke test and games still run)."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1800
    run.apply_iteration(1, IterationResult(False, "gate:boom"))
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1], detail="gate: boom"))
    run.request_stop()
    assert plain(story.now_line(run, clock.t)) == "■ Stopping…"


def test_a_single_identity_is_not_pluralized():
    """Ruling 10 minor: "your 1 identities" -> "your 1 identity"."""
    clock = Clock()
    run = Run("r1", EvolveConfig(IDS[0], "codex", 5), clock=clock)
    clock.t = 1004
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Fetching the best bots for your 1 identity from the hub… · 4s")


# ---- finding 7: §6.1/§6.2/§6.3/§6.5 states the committed suite never reached ------

def test_the_now_line_during_the_smoke_test():
    """§6.1 "iteration, smoke test" row -- untested: every existing test that
    reaches this window only checks section_view, never now_line."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1612
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Iteration 1 of 5 · smoke test: one short game to check the edited bot runs · 12s")


def test_the_now_line_and_section_view_while_playing_and_while_deciding():
    """§6.1 "iteration, games" (a non-zero count) and "iteration, deciding"
    rows, plus §6.2's own "deciding | running" step row -- none of the
    committed tests call now_line, or check the deciding step row via
    section_view, during either window."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1620
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1630
    _ep(run, "iter 1/5 · dev", 0, 45, IDS[1], 0.30)
    clock.t = 1650
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Iteration 1 of 5 · playing the edited bot · 1/45 games · avg 0.30 · 30s")
    for i in range(1, 45):
        _ep(run, "iter 1/5 · dev", i, 45, IDS[i % 3], 0.30)
    clock.t = 1700
    assert plain(story.now_line(run, clock.t)) == (
        "▶ Iteration 1 of 5 · comparing with the best so far · sending to the hub…")
    decide_row = story.section_view(run, 1, clock.t).rows[-1]
    assert plain(decide_row.label) == "comparing with the best so far · sending to the hub…"
    assert plain(decide_row.mark) == "▶"


def test_stopping_after_games_have_already_started():
    """§6.1's "stopping, during the smoke test or games" row -- distinct from
    the "stopping, during an edit" row test_stopping_says_what_still_runs
    already covers; untested until now."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1600
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    clock.t = 1620
    run.apply_state(_state("evaluating-dev", 1, cells=CELLS, cell=IDS[1]))
    _ep(run, "iter 1/5 · dev", 0, 45, IDS[1], 0.25)
    run.request_stop()
    assert plain(story.now_line(run, clock.t)) == (
        "■ Stopping after this iteration's games · 1/45 games")


def test_the_now_line_during_the_seed_and_union_setup_batches():
    """§6.1's seed-batch and union-batch now-line rows -- the only setup
    now-line row the committed suite reaches is a champion batch's."""
    clock = Clock()
    run = _new(clock)
    clock.t = 1006
    elite = {IDS[0]: {"program_id": CLYDE, "score": 0.22},
             IDS[1]: {"program_id": CLYDE, "score": 0.19}}
    run.apply_state(_state("cold-start", elite_of=elite))
    clock.t = 1100
    for i in range(30):
        _ep(run, "cold-start · dev [aaaa1111]", i, 30, IDS[i % 2], 0.2)
    clock.t = 1140
    for i in range(4):
        _ep(run, "cold-start · dev [seed]", i, 15, IDS[2], 0.1)
    assert plain(story.now_line(run, 1140)) == (
        "▶ Setup · playing the starting bot on the 1 identity with no hub champion"
        " · 4/15 games · 40s")
    clock.t = 1200
    for i in range(4, 15):
        _ep(run, "cold-start · dev [seed]", i, 15, IDS[2], 0.1)
    run.apply_state(_state("cold-start", elite_of=elite, cells=CELLS))
    for i in range(12):
        _ep(run, "cold-start · dev [union]", i, 45, IDS[i % 3], 0.3)
    assert plain(story.now_line(run, 1263)) == (
        "▶ Setup · playing the best bot on average on all 3 identities · 12/45 games · 1m 03s")


def test_an_agent_crash_is_worded_consistently_across_the_view():
    """§6.2's "edit | agent error" row, §6.3's "agent failed" label and
    §6.5's "agent failed" cell -- an operator-error (the agent's OWN process
    crashing), distinct from the generic loop "error:" already covered
    above; none of the three were tested anywhere in the committed suite."""
    clock = Clock()
    run = _through_setup(clock)
    run.apply_log(run.tag(1), _codex_edit("/w/bot.py"))
    clock.t = 1620
    run.apply_state(_state("error", 1, cells=CELLS, detail="exit 1"))
    run.apply_iteration(1, IterationResult(False, "operator-error:exit 1"))
    view = story.section_view(run, 1, clock.t)
    assert [plain(r.label) for r in view.rows] == ["the agent failed: exit 1"]
    assert plain(view.rows[0].mark) == "✗"
    assert plain(story.iter_label(run, 1)[0]) == "✗ iter 1   agent failed  2m"
    assert plain(story.this_cell(run, IDS[1], 1, 0.18)[0]) == "— agent failed"


def test_an_agent_killed_by_stop_via_section_view():
    """§6.2's "edit | killed by Stop" row -- untested until now."""
    clock = Clock()
    run = _through_setup(clock)
    run.apply_log(run.tag(1), _codex_edit("/w/bot.py"))
    run.request_stop()
    clock.t = 1620
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    run.apply_iteration(1, IterationResult(
        True, "registered", dev_fitness=0.2, improved=[IDS[1]], stopped_reason="killed",
        results=[{"character": IDS[1], "progress": 0.2}]))
    run.apply_state(_state("registered", 1, cells=CELLS, cell=IDS[1]))
    view = story.section_view(run, 1, clock.t)
    assert plain(view.rows[0].label) == "the agent was stopped · 1 action"
    assert plain(view.rows[0].mark) == "■"


def test_this_cell_for_a_failed_smoke_test_and_a_stopped_iteration():
    """§6.5's "failed smoke test" and "stopped" cells -- untested until now."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1560
    run.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    run.apply_iteration(1, IterationResult(False, "gate:smoke episode crashed"))
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1],
                           detail="gate: smoke episode crashed"))
    assert plain(story.this_cell(run, IDS[1], 1, 0.18)[0]) == "— failed smoke test"

    stop_clock = Clock()
    stopped = _through_setup(stop_clock)
    stop_clock.t = 1560
    stopped.apply_state(_state("gating", 1, cells=CELLS, cell=IDS[1]))
    stopped.request_stop()
    stopped.finish()
    assert plain(story.this_cell(stopped, IDS[1], 1, 0.18)[0]) == "— stopped"


def test_iter_label_no_gain_and_not_run_after_a_stop():
    """§6.3's "no gain" and "not run" (a later, never-started iteration after
    a stop, disabled) labels -- untested until now."""
    clock = Clock()
    run = _through_setup(clock)
    clock.t = 1700
    run.apply_iteration(1, IterationResult(False, "no-cell-improved", dev_fitness=0.12))
    run.apply_state(_state("rejected", 1, cells=CELLS, cell=IDS[1]))
    assert plain(story.iter_label(run, 1)[0]) == "✗ iter 1   no gain  3m"
    run.request_stop()
    run.finish()
    label, disabled = story.iter_label(run, 3)
    assert plain(label) == "·  iter 3   not run" and disabled is True
