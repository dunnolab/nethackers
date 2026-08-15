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


def test_apply_episode_orders_by_index_and_counts_completed():
    r = Run("r1", CFG)
    for idx in (2, 0, 1):  # arrival order != batch order (parallel eval)
        r.apply_episode("iter 1/3 · dev", {
            "index": idx, "total": 3, "seed": idx, "character": "val-dwa-law-fem",
            "progress": 0.1 * idx, "status": "completed", "turns": 1, "depth": 1})
    batch = r.current_batch()
    assert [row["index"] for row in batch.rows()] == [0, 1, 2]  # reads in batch order
    assert r.eval_step is not None
    assert r.eval_step[:2] == (3, 3)                             # completed-count, not index
    assert round(r.eval_step[2], 3) == 0.1                       # mean(0.0, 0.1, 0.2)
    r.apply_episode("iter 1/3 · held", {  # a new label seals the previous batch
        "index": 0, "total": 2, "seed": 0, "character": "val-dwa-law-fem",
        "progress": 0.5, "status": "completed", "turns": 1, "depth": 1})
    assert len(r.batches) == 2 and r.batches[0].done is True


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
    ok.finish(results=["x"])
    assert ok.status == "done" and ok.running is False
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
