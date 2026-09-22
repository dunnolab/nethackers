"""Wording tests for tui.story -- the evolve monitor's plain-language narration.
Built on Runs driven through their real apply_* reductions with a fake clock,
so every duration is exact."""
from __future__ import annotations

import json

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
