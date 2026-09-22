"""Pure-formatter tests for tui.status: the shared _bar helper plus the
monitor rework's own formatters (dur, best_cell, mutator_title,
status_line) -- what tui.screens.monitor.RunMonitor renders through.
"""
from nethackers.harness.metering import TokenUsage
from nethackers.tui import status as S
from nethackers.tui.status import EvolveConfig, _bar

# ---- _bar -------------------------------------------------------------


def test_bar_fills_proportionally_to_fraction():
    assert _bar(0.0, width=8) == "░░░░░░░░"
    assert _bar(1.0, width=8) == "▓▓▓▓▓▓▓▓"
    assert _bar(0.38, width=8) == "▓▓▓░░░░░"  # round(0.38*8) == 3


def test_bar_clamps_out_of_range_fractions():
    assert _bar(-1.0, width=4) == "░░░░"
    assert _bar(2.0, width=4) == "▓▓▓▓"


# ---- dur -------------------------------------------------------


def test_dur_is_hours_and_minutes_no_seconds():
    assert S.dur(65) == "1m"
    assert S.dur(3 * 3600 + 25 * 60 + 9) == "3h 25m"


def test_ep_time_is_seconds_under_a_minute_else_minutes_and_seconds():
    # per-episode clock time (DetailView's per-seed "time" column) needs
    # second-granularity -- unlike dur() (hours/minutes, for TOTAL run time),
    # a 9-second episode must not round down to "0m".
    assert S.ep_time(9) == "9s"
    assert S.ep_time(134) == "2m 14s"
    assert S.ep_time(60) == "1m 00s"
    # production always passes floats (wall_seconds) -- lock int(round()) too.
    assert S.ep_time(9.0) == "9s"
    assert S.ep_time(134.0) == "2m 14s"


def test_best_cell_colors_by_kind_and_shows_score():
    txt = str(S.best_cell((0.42, "clyde @11", "hub", None)))
    assert "clyde @11" in txt and "0.42" in txt


def test_mutator_title_includes_agent_version_model_effort():
    cfg = EvolveConfig("v1,v2", "claude", 3, model="opus", effort="high",
                       operator_version="1.2.7")
    txt = str(S.mutator_title(cfg))
    assert "Claude Code" in txt and "1.2.7" in txt and "opus" in txt and "high" in txt


def test_status_line_leads_with_orientation_and_ends_with_tokens():
    usage = TokenUsage(3_600_000, 132_000, 410_000, 12_300_000)
    txt = S.status_line("iter 1", "iter 4", 1, 5, 2160, usage)
    assert txt.strip().startswith("viewing iter 1 (live: iter 4)")
    assert "1 of 5 improved" in txt and "run time 36m" in txt
    assert "tokens in 3.6M" in txt and txt.rstrip().endswith("read 12.3M")
    assert "(live:" not in S.status_line("iter 4", None, 1, 5, 2160, usage)


def test_short_time_is_seconds_then_minutes_then_hours():
    assert S.short_time(45) == "45s"
    assert S.short_time(420) == "7m"
    assert S.short_time(3840) == "1h 04m"


def test_agent_name_covers_every_operator():
    assert S.agent_name(EvolveConfig("x", "opencode2", 1)) == "OpenCode"
    assert S.agent_name(EvolveConfig("x", "codex", 1)) == "Codex"
    assert S.agent_name(EvolveConfig("x", "claude", 1)) == "Claude Code"
