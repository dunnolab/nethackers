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


def test_apply_state_captures_origins_and_baseline():
    r = Run("r1", CFG)
    r.apply_state(_state("cold-start",
                         origins={"d1": {"kind": "hub", "handle": "clyde", "sha": "11",
                                         "repo": "github.com/t/a", "iteration": None}},
                         aa_baseline={"val-dwa-law-fem": 0.28}))
    assert r.origins()["d1"]["handle"] == "clyde"
    assert r.aa_baseline()["val-dwa-law-fem"] == 0.28


def test_roles_and_token_usage():
    r = Run("r1", EvolveConfig("wiz-elf-cha-mal,val-dwa-law-fem,wiz-orc-cha-mal", "claude", 3))
    r.apply_state(_state("cold-start",
                         identities=["wiz-elf-cha-mal", "val-dwa-law-fem", "wiz-orc-cha-mal"]))
    assert r.roles_present() == ["wiz", "val"]       # first-seen order, deduped
    assert r.role_of("val-dwa-law-fem") == "val"
    r.apply_log("iter 1/3", '{"type":"result","usage":{"input_tokens":10,"output_tokens":5,'
                            '"cache_creation_input_tokens":3,"cache_read_input_tokens":100}}')
    u = r.token_usage()
    assert (u.input, u.output, u.cache_creation, u.cache_read) == (10, 5, 3, 100)


def _cold(**kw):
    base = dict(phase="cold-start", iteration=0, identities=["v1", "v2"],
                cells=[{"identity": "v1", "score": 0.42, "digest": "d1"}],  # v1 has a champion
                origins={"d1": {"kind": "hub", "handle": "clyde", "sha": "11",
                                "repo": "github.com/t/a", "iteration": None}},
                aa_baseline={"v1": 0.28, "v2": 0.31}, union=None, cell_results={})
    base.update(kw)
    return base


def test_incumbent_starts_at_champion_or_autoascend_and_propagates():
    from nethackers.harness.loop import IterationResult
    r = Run("r1", EvolveConfig("v1,v2", "claude", 5))
    r.apply_state(_cold())
    assert r.incumbent("v1", upto_k=1) == (0.42, "clyde @11", "hub", None)
    assert r.incumbent("v2", upto_k=1) == (0.31, "AutoAscend", "aa", None)
    # iteration 1 registers, beating v1's champion on v1
    r.apply_iteration(1, IterationResult(True, "registered", improved=["v1"],
                                         results=[{"character": "v1", "progress": 0.5}]))
    score, label, kind, j = r.incumbent("v1", upto_k=2)
    assert (round(score, 2), kind, j) == (0.5, "run", 1) and label == "run · iter 1"


def test_incumbent_reads_the_cold_start_snapshot_not_live_state():
    """Regression: once a run child takes a cell, state["cells"]/["origins"]
    are OVERWRITTEN to the latest archive on every on_state emit -- so if
    incumbent() read them live, a past-iteration view (upto_k=1, i.e. BEFORE
    the child's win) would see the child's digest (kind "run") instead of the
    hub champion that was actually incumbent at that point, and wrongly
    collapse to AutoAscend. incumbent() must read the FROZEN cold-start
    snapshot (init_cells/origins as of cold-start) for the starting point,
    only propagating forward through completed iterations."""
    from nethackers.harness.loop import IterationResult
    r = Run("r1", EvolveConfig("v1", "claude", 5))
    hub_origin = {"kind": "hub", "handle": "clyde", "sha": "11",
                  "repo": "github.com/t/a", "iteration": None}
    r.apply_state({
        "phase": "cold-start", "iteration": 0,
        "cells": [{"identity": "v1", "score": 0.42, "digest": "d1"}],
        "origins": {"d1": hub_origin}, "aa_baseline": {"v1": 0.28},
        "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0,
        "detail": "", "parent_digest": "", "parent_dev": 0.0, "generation": 0})
    # a run child later takes v1's cell -- the LIVE cells/origins now show the
    # child (kind "run"), overwriting the cold-start snapshot in self.state.
    run_origin = {"kind": "run", "handle": "dev", "sha": None, "repo": None, "iteration": 1}
    r.apply_state({
        "phase": "registered", "iteration": 1,
        "cells": [{"identity": "v1", "score": 0.6, "digest": "child1"}],
        "origins": {"d1": hub_origin, "child1": run_origin},  # merged, as a real emit would be
        "aa_baseline": {"v1": 0.28},
        "baseline_dev": 0.0, "best_dev": 0.0, "wins": 1, "tokens": 0,
        "detail": "", "parent_digest": "", "parent_dev": 0.0, "generation": 1})
    r.apply_iteration(1, IterationResult(True, "registered", improved=["v1"],
                                         results=[{"character": "v1", "progress": 0.6}]))
    # going INTO iter 1 (i.e. before the win is folded in): still the hub
    # champion -- NOT AutoAscend, which is what reading live state would give.
    assert r.incumbent("v1", upto_k=1) == (0.42, "clyde @11", "hub", None)
    # after iter 1: propagated to the run win, as before.
    assert r.incumbent("v1", upto_k=2) == (0.6, "run · iter 1", "run", 1)


def test_best_overall_uses_union_then_propagates_and_falls_back():
    from nethackers.harness.loop import IterationResult
    r = Run("r1", EvolveConfig("v1,v2", "claude", 5))
    # no union seeded -> AutoAscend overall = mean of baselines
    r.apply_state(_cold())
    score, label, kind, j = r.best_overall(upto_k=1)
    # round: sum(0.28, 0.31) / 2 is 0.29500000000000004 in IEEE-754, not 0.295
    assert (round(score, 3), label, kind, j) == (0.295, "AutoAscend", "aa", None)
    # union seeded from the hub board champion
    r.apply_state(_cold(union={"score": 0.48, "digest": "u1"},
                        origins={"u1": {"kind": "hub", "handle": "clyde", "sha": "33",
                                        "repo": "github.com/t/u", "iteration": None}}))
    assert r.best_overall(upto_k=1) == (0.48, "clyde @33", "hub", None)
    # a child takes the union cell in iter 1 (dev_fitness = union mean)
    r.apply_iteration(1, IterationResult(True, "registered", improved=["v1", "union"],
                                         dev_fitness=0.55, results=[{"character": "v1",
                                                                     "progress": 0.55}]))
    score, label, kind, j = r.best_overall(upto_k=2)
    assert (round(score, 2), kind, j) == (0.55, "run", 1) and label == "run · iter 1"


def test_iteration_evals_init_completed_running():
    from nethackers.harness.loop import IterationResult
    r = Run("r1", EvolveConfig("v1,v2", "claude", 5))
    r.apply_state(_cold(cell_results={"v1": [{"character": "v1", "trajectory_id": 7,
                        "progress": 0.4, "status": "completed", "end_status": "died",
                        "ascended": False, "cause_of_death": "killed by a newt",
                        "max_depth": 6, "turns": 900, "wall_seconds": 12.0}]}))
    ev0 = r.iteration_evals(0)["v1"]
    assert ev0.rows[0]["cause"] == "killed by a newt" and ev0.rows[0]["seed"] == 7
    # completed iteration reads iter_results[k].results
    r.apply_iteration(1, IterationResult(True, "registered", improved=["v1"],
                                         results=[{"character": "v1", "trajectory_id": 0,
                                                   "progress": 0.6, "status": "completed",
                                                   "end_status": "died", "ascended": False,
                                                   "cause_of_death": "starvation",
                                                   "max_depth": 8, "turns": 1200,
                                                   "wall_seconds": 20.0}]))
    assert r.iteration_evals(1)["v1"].rows[0]["cause"] == "starvation"
    # iteration 2 starts running with its own live batch...
    r.apply_state(_state("evaluating-dev", iteration=2, identities=["v1", "v2"]))
    r.apply_episode("iter 2/5 · dev", {"index": 0, "total": 2, "seed": 10,
                                       "character": "v1", "progress": 0.3,
                                       "status": "died", "turns": 400, "depth": 3})
    r.apply_episode("iter 2/5 · dev", {"index": 1, "total": 2, "seed": 11,
                                       "character": "v1", "progress": 0.5,
                                       "status": "died", "turns": 500, "depth": 4})
    # ...while iteration 1 is (re)recorded as a gate reject (results=None): it
    # must show NO rows, never iteration 2's live batch mislabeled as its own.
    r.apply_iteration(1, IterationResult(False, "gate:smoke", results=None))
    assert r.iteration_evals(1)["v1"].rows == []
    assert len(r.iteration_evals(2)["v1"].rows) == 2   # the actually-running iteration


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
