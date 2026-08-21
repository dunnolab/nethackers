"""Pure-formatter tests for the evolve-monitor screen's building blocks:
parent_panel, candidate_line, eval_line, lineage_strip, iterations_ledger,
status_line, and the _bar helper they share -- what tui.screens.evolve.
EvolveScreen renders through. These superseded the old single-panel
format_status(), which the old EvolveApp used; both were removed in the
Task 10 NetHackersApp shell cutover.
"""
from nethackers.tui.status import (
    EvolveConfig,
    _bar,
    candidate_line,
    eval_line,
    iterations_ledger,
    lineage_strip,
    parent_panel,
    scorecard,
    status_line,
)

CFG = EvolveConfig(objective="wiz-elf-cha-mal", backend="claude", iterations=3)


def _state(phase="mutating", **kw):
    base = {"phase": phase, "iteration": 2, "baseline_dev": 0.30, "baseline_held": 0.28,
            "best_dev": 0.44, "best_held": 0.41, "wins": 1, "tokens": 0, "detail": "",
            "parent_digest": "7a3f1122", "parent_dev": 0.30, "parent_held": 0.28,
            "generation": 1}
    base.update(kw)
    return base


# ---- _bar -------------------------------------------------------------

def test_bar_fills_proportionally_to_fraction():
    assert _bar(0.0, width=8) == "░░░░░░░░"
    assert _bar(1.0, width=8) == "▓▓▓▓▓▓▓▓"
    assert _bar(0.38, width=8) == "▓▓▓░░░░░"  # round(0.38*8) == 3


def test_bar_clamps_out_of_range_fractions():
    assert _bar(-1.0, width=4) == "░░░░"
    assert _bar(2.0, width=4) == "▓▓▓▓"


# ---- parent_panel -------------------------------------------------------

def test_parent_panel():
    s = parent_panel(_state())
    assert s == "PARENT  #7a3f · gen 1 · dev 0.30  held 0.28"
    assert "#7a3f" in s and "gen 1" in s and "dev 0.30" in s and "held 0.28" in s


def test_parent_panel_shows_seed_when_no_parent_digest():
    s = parent_panel(_state(parent_digest="", generation=0))
    assert s == "PARENT  seed · gen 0 · dev 0.30  held 0.28"
    assert "seed" in s and "gen 0" in s


# ---- candidate_line -------------------------------------------------------

def test_candidate_line_has_tokens_and_clock():
    s = candidate_line(_state(), live_tokens=92_000, elapsed_s=194)
    assert s == "mutating ⠙  92.0k tok · 3:14"
    assert "92.0k tok" in s and "3:14" in s


def test_candidate_line_maps_known_phase_verbs():
    kw = {"live_tokens": 0, "elapsed_s": 0}
    assert candidate_line(_state(phase="gating"), **kw).startswith("gating ⠙")
    assert candidate_line(_state(phase="evaluating-dev"), **kw).startswith("eval dev ⠙")
    assert candidate_line(_state(phase="evaluating-held"), **kw).startswith("eval held ⠙")


def test_candidate_line_falls_back_to_raw_phase_when_unmapped():
    s = candidate_line(_state(phase="registered"), live_tokens=0, elapsed_s=0)
    assert s.startswith("registered ⠙")


# ---- eval_line -------------------------------------------------------

def test_eval_line_pending_when_step_is_none():
    s = eval_line("dev", None, {})
    assert s == "eval · dev  ┄ pending"


def test_eval_line_bar_mean_counts():
    s = eval_line("dev", (2, 8, 0.38), {"ascended": 1, "died": 2})
    assert s == "eval · dev  ▓▓▓░░░░░  x̄0.38  2/8  ★1  ☠2"
    assert "2/8" in s and "0.38" in s
    assert "★1" in s and "☠2" in s


def test_eval_line_omits_zero_count_statuses():
    s = eval_line("held", (1, 3, 0.5), {"ascended": 0, "completed": 1, "died": 0})
    assert "✓1" in s
    assert "★" not in s and "☠" not in s


# ---- lineage_strip -------------------------------------------------------

def test_lineage_strip_delta():
    s = lineage_strip(["seed", "a1b2", "7a3f"], best_dev=0.44, baseline_dev=0.30)
    assert s == "lineage  seed → #a1b2 → #7a3f → ?     best dev 0.44  Δ+0.14"
    assert "seed" in s and "#7a3f" in s and "+0.14" in s


def test_lineage_strip_negative_delta_when_below_baseline():
    s = lineage_strip(["seed"], best_dev=0.20, baseline_dev=0.30)
    assert "Δ-0.10" in s


# ---- iterations_ledger -------------------------------------------------------

def test_iterations_ledger():
    s = iterations_ledger([(1, False, "no-dev-gain"), (2, True, "registered")])
    assert s == "1 ✗ no-dev-gain   2 ✓ registered"
    assert "1" in s and "no-dev-gain" in s and "2" in s


def test_iterations_ledger_empty_shows_dash():
    assert iterations_ledger([]) == "—"


# ---- status_line -------------------------------------------------------

def test_status_line_motif():
    s = status_line(CFG, _state(), live_tokens=92_000, elapsed_s=194)
    assert s == "wiz-elf-cha-mal  gen:1  tok:92.0k  T:3:14  best:Dlvl:23  w:1"
    assert "wiz-elf-cha-mal" in s and "gen:1" in s and "tok:92" in s and "w:1" in s
    assert "best:Dlvl" in s


# ---- scorecard -------------------------------------------------------

def test_scorecard_weakest_first_with_floor_header():
    means = {"wiz-elf-cha-mal": 0.5, "wiz-orc-cha-mal": 0.1, "wiz-gno-neu-fem": 0.3}
    text = scorecard(means, None, list(means))
    # weakest build listed first
    assert text.index("wiz-orc-cha-mal") < text.index("wiz-elf-cha-mal")
    assert "floor" in text and "coverage 3/3" in text


def test_scorecard_shows_delta_when_candidate_present():
    parent = {"a": 0.2, "b": 0.4}
    cand = {"a": 0.5, "b": 0.3}
    text = scorecard(parent, cand, ["a", "b"])
    assert "+0.30" in text or "+0.3" in text  # a improved
