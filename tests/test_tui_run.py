"""Unit tests for the app-owned run state (tui.run.Run)."""
from __future__ import annotations

from nethackers.tui.run import Run
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig("val-dwa-law-fem", "claude", 3)


def _state(phase, **kw):
    base = {"phase": phase, "iteration": 1, "baseline_dev": 0.3, "baseline_held": 0.28,
            "best_dev": 0.44, "best_held": 0.41, "wins": 1, "tokens": 0, "detail": "",
            "parent_digest": "7a3f", "parent_dev": 0.3, "parent_held": 0.28, "generation": 1}
    base.update(kw)
    return base


def test_apply_state_builds_chain_ledger_and_selects_log():
    r = Run("r1", CFG)
    r.apply_state(_state("cold-start", parent_digest="seed0"))
    r.apply_state(_state("mutating", iteration=1, parent_digest="seed0"))
    assert r.sel_tag == "iter 1/3" and "iter 1/3" in r.logs  # mutating selects its log
    r.apply_state(_state("registered", iteration=1, parent_digest="elite1"))
    r.apply_state(_state("mutating", iteration=2, parent_digest="elite1"))
    r.apply_state(_state("rejected", iteration=2, detail="no dev gain", parent_digest="elite1"))
    assert r.chain == ["seed0", "elite1"]  # consecutive-parent dedup
    assert r.ledger_rows == [(1, True, "registered"), (2, False, "no dev gain")]


def test_apply_state_registered_with_regression_detail_marks_the_ledger_reason():
    # a "registered" state whose detail carries the regression-count marker
    # (loop._emit's f"⚠{len(regs)}") must fold it into the ledger reason, so
    # status.iterations_ledger's plain "{k} {✓/✗} {reason}" render surfaces it.
    r = Run("r1", CFG)
    r.apply_state(_state("registered", iteration=1, detail="⚠2"))
    assert r.ledger_rows == [(1, True, "registered ⚠2")]


def test_apply_state_registered_without_detail_keeps_the_plain_reason():
    # single-identity wins never carry a detail marker -- the reason must stay
    # exactly "registered" (existing behavior), not "registered " with a
    # trailing space.
    r = Run("r1", CFG)
    r.apply_state(_state("registered", iteration=1, detail=""))
    assert r.ledger_rows == [(1, True, "registered")]


def test_apply_state_registered_with_hub_reason_shows_local_only_in_the_ledger():
    # A win that never reached the hub must be VISIBLE as such in the ledger
    # -- "registered" alone would silently overstate what happened (the bug
    # this whole adapter exists to stop).
    r = Run("r1", CFG)
    r.apply_state(_state(
        "registered", iteration=1,
        hub_reason="local-only: not published (no gh publisher / dev owner)"))
    assert r.ledger_rows == [
        (1, True, "local-only: not published (no gh publisher / dev owner)")]


def test_apply_state_registered_hub_reason_and_regression_detail_combine():
    # hub_reason (why it's local-only) and detail (the regression-count
    # marker) are orthogonal -- both must survive in the ledger line.
    r = Run("r1", CFG)
    r.apply_state(_state(
        "registered", iteration=1, detail="⚠2",
        hub_reason="local-only: hub error — boom"))
    assert r.ledger_rows == [(1, True, "local-only: hub error — boom ⚠2")]


def test_apply_state_registered_without_hub_reason_is_unaffected():
    # No hub_reason key at all (older state payload shape) must behave
    # exactly like hub_reason=None -- plain "registered", never a KeyError.
    r = Run("r1", CFG)
    state = _state("registered", iteration=1, detail="")
    assert "hub_reason" not in state
    r.apply_state(state)
    assert r.ledger_rows == [(1, True, "registered")]


def test_apply_episode_orders_by_index_and_counts_completed():
    r = Run("r1", CFG)
    for idx in (2, 0, 1):  # arrival order != batch order (parallel eval)
        r.apply_episode("iter 1/3 · dev", {
            "index": idx, "total": 3, "seed": idx, "character": "val-dwa-law-fem",
            "progress": 0.1 * idx, "status": "completed", "turns": 1, "depth": 1})
    batch = r.current_batch()
    assert [row["index"] for row in batch.rows()] == [0, 1, 2]  # reads in batch order
    assert batch.done is True                                  # seals on final arrival
    assert r.eval_step is not None
    assert r.eval_step[:2] == (3, 3)                             # completed-count, not index
    assert round(r.eval_step[2], 3) == 0.1                       # mean(0.0, 0.1, 0.2)
    r.apply_episode("iter 1/3 · held", {  # a new label seals the previous batch
        "index": 0, "total": 2, "seed": 0, "character": "val-dwa-law-fem",
        "progress": 0.5, "status": "completed", "turns": 1, "depth": 1})
    assert len(r.batches) == 2 and r.batches[0].done is True
    assert r.batches[1].done is False


def test_apply_log_extends_logs_and_meters_faithful_tokens():
    r = Run("r1", CFG)
    r.apply_log("iter 1/3",
                '{"type":"assistant","message":{"content":[{"type":"text","text":"edit"}]}}')
    assert r.logs["iter 1/3"]  # prettified lines stored for backfill
    r.apply_log("iter 1/3",
                '{"type":"result","usage":{"input_tokens":1,"output_tokens":2,'
                '"cache_read_input_tokens":3,"cache_creation_input_tokens":4}}')
    assert r.meters["iter 1/3"].usage.total == 10  # faithful cache-aware total


def test_live_tokens_tracks_the_running_iteration():
    r = Run("r1", CFG)
    r.apply_state(_state("mutating", iteration=1))
    r.apply_log("iter 1/3",
                '{"type":"result","usage":{"input_tokens":50000,"output_tokens":42000,'
                '"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}')
    assert r.running_tag() == "iter 1/3" and r.live_tokens() == 92000


def test_finish_sets_status():
    ok = Run("a", CFG)
    ok.apply_episode("cold-start · held", {
        "index": 0, "total": 1, "seed": 1000, "character": "val-dwa-law-fem",
        "progress": 0.1, "status": "completed", "turns": 1, "depth": 1})
    ok.finish(results=["x"])
    assert ok.status == "done" and ok.running is False
    assert ok.current_batch().done is True
    bad = Run("b", CFG)
    bad.finish(error=RuntimeError("boom"))
    assert bad.status == "failed"
    st = Run("c", CFG)
    st.stop.set()
    st.finish()
    assert st.status == "stopped"


def test_split_from_phase_then_batch_label():
    r = Run("r1", CFG)
    r.apply_state(_state("evaluating-dev"))
    assert r.split() == "dev"
    r.apply_state(_state("evaluating-held"))
    assert r.split() == "held"
    r.apply_state(_state("gating"))
    r.apply_episode("iter 1/3 · held-out", {
        "index": 0, "total": 1, "seed": 0, "character": "val-dwa-law-fem",
        "progress": 0.1, "status": "completed", "turns": 1, "depth": 1})
    assert r.split() == "held"  # falls back to the current batch label


def test_candidate_means_groups_current_batch_rows_by_character():
    r = Run("r1", CFG)
    r.apply_episode("iter 1/3 · dev", {"index": 0, "total": 3, "progress": 0.2,
                                        "status": "died", "character": "wiz-elf-cha-mal"})
    r.apply_episode("iter 1/3 · dev", {"index": 1, "total": 3, "progress": 0.4,
                                        "status": "died", "character": "wiz-elf-cha-mal"})
    r.apply_episode("iter 1/3 · dev", {"index": 2, "total": 3, "progress": 0.6,
                                        "status": "died", "character": "wiz-orc-cha-mal"})
    means = r.candidate_means()
    assert set(means) == {"wiz-elf-cha-mal", "wiz-orc-cha-mal"}
    assert round(means["wiz-elf-cha-mal"], 3) == 0.3  # mean(0.2, 0.4)
    assert round(means["wiz-orc-cha-mal"], 3) == 0.6


def test_candidate_means_empty_when_no_batch_yet():
    r = Run("r1", CFG)
    assert r.candidate_means() == {}


def test_identities_and_parent_means_read_from_state():
    r = Run("r1", CFG)
    r.apply_state(_state("mutating", identities=["a", "b"], parent_means={"a": 0.1, "b": 0.2}))
    assert r.identities() == ["a", "b"]
    assert r.parent_means() == {"a": 0.1, "b": 0.2}


def test_identities_and_parent_means_default_empty_when_absent():
    r = Run("r1", CFG)
    r.apply_state(_state("mutating"))  # single/random objectives never set these keys
    assert r.identities() == []
    assert r.parent_means() == {}


def test_multi_identity_run_does_not_build_the_single_lineage_chain():
    """A set run (len(identities) > 1): the loop picks a random cell each
    iteration, so the single-lineage chain must stay empty (the cell-archive
    panel replaces it). A single-identity run still builds the chain with
    consecutive-parent dedup."""
    r = Run("ri", CFG)
    r.apply_state(_state("gating", parent_digest="cellA", identities=["a", "b"]))
    r.apply_state(_state("gating", parent_digest="cellB", identities=["a", "b"]))
    r.apply_state(_state("gating", parent_digest="cellA", identities=["a", "b"]))
    assert r.chain == []   # no fake chain spliced from separate cells

    r1 = Run("r1", CFG)
    r1.apply_state(_state("gating", parent_digest="seed0", identities=["a"]))
    r1.apply_state(_state("gating", parent_digest="seed0", identities=["a"]))   # same -> dedup
    r1.apply_state(_state("gating", parent_digest="elite1", identities=["a"]))
    assert r1.chain == ["seed0", "elite1"]   # single-lineage chain intact


def test_apply_iteration_stores_result():
    from nethackers.harness.loop import IterationResult
    r = Run("r1", CFG)
    r.apply_iteration(2, IterationResult(True, "registered", dev_fitness=0.5,
                                         improved=["val-dwa-law-fem", "union"]))
    assert r.iter_results[2].dev_fitness == 0.5
    assert "union" in r.iter_results[2].improved


def test_run_tracks_cells_and_coverage_from_state():
    r = Run("r1", EvolveConfig("wiz-elf-cha-mal,wiz-orc-cha-mal", "claude", 3))
    r.apply_state({
        "phase": "mutating", "iteration": 1, "baseline_dev": 0.2, "wins": 0,
        "tokens": 0, "detail": "", "hub_reason": None, "generation": 1,
        "parent_digest": "sha256:aaaa", "parent_dev": 0.9,
        "identities": ["wiz-elf-cha-mal", "wiz-orc-cha-mal"],
        "cell": "wiz-orc-cha-mal", "coverage": (1, 2),
        "cells": [{"identity": "wiz-elf-cha-mal", "score": 0.9, "digest": "sha256:aaaa"},
                  {"identity": "wiz-orc-cha-mal", "score": 0.3, "digest": "sha256:bbbb"}]})
    assert r.cells()[0]["identity"] == "wiz-elf-cha-mal"
    assert r.coverage() == (1, 2)
    assert r.active_cell() == "wiz-orc-cha-mal"
    assert r.chain == []   # set run: no single-lineage chain
