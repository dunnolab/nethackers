import asyncio

from nethackers.tui.app import EvolveApp, _rows_in_order
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig(objective="val-dwa-law-fem", backend="claude", iterations=3)


def test_rows_in_order_sorts_by_index_regardless_of_arrival():
    rows = {}
    for idx in (2, 0, 1):  # out-of-order completion
        rows[idx] = {"index": idx, "seed": 100 + idx, "progress": 0.1 * idx}
    ordered = _rows_in_order(rows)
    assert [r["index"] for r in ordered] == [0, 1, 2]


def _state(phase, **kw):
    base = {"phase": phase, "iteration": 1, "baseline_dev": 0.07, "baseline_held": 0.05,
            "best_dev": 0.09, "best_held": 0.06, "wins": 1, "tokens": 0, "detail": ""}
    base.update(kw)
    return base


async def test_status_bar_updates_from_state():
    app = EvolveApp(CFG, run=None)
    async with app.run_test() as pilot:
        app._apply_state(_state("mutating", iteration=2))
        await pilot.pause()
        text = str(app.query_one("#status").content)
        assert "MUTATING iter 2/3" in text
        assert "best dev 0.090" in text


async def test_episode_mounts_table_and_log_records_line():
    app = EvolveApp(CFG, run=None)
    async with app.run_test() as pilot:
        app._apply_state(_state("mutating", iteration=1))
        app._apply_log("iter 1/3",
                       '{"type":"assistant","message":{"content":'
                       '[{"type":"text","text":"editing bot"}]}}')
        app._apply_episode("iter 1/3 · dev", {
            "index": 1, "total": 8, "seed": 0, "character": "val-dwa-law-fem",
            "progress": 0.1, "status": "completed", "turns": 5, "depth": 1})
        await pilot.pause()
        assert app.query("#tables Static")               # a batch table mounted
        assert app._logs["iter 1/3"] == [("assistant", "editing bot")]
        app._apply_log("iter 1/3",
                       '{"type":"result","usage":{"input_tokens":1,"output_tokens":2,'
                       '"cache_read_input_tokens":3,"cache_creation_input_tokens":4}}')
        assert app._meters["iter 1/3"].usage.total == 10  # faithful meter from the result line


async def test_apply_episode_out_of_order_arrival_sorts_rows_and_counts_done():
    """M3: parallel eval means episodes can complete out of order (e.g. seed
    2 before seed 0). The table must still read in batch order, and the
    status line's eval_step must report how many episodes have actually
    finished (a true completed-count), not the arriving episode's own
    (no-longer-monotonic) index."""
    app = EvolveApp(CFG, run=None)
    async with app.run_test() as pilot:
        app._apply_state(_state("evaluating-dev", iteration=1))
        for idx in (2, 0, 1):  # arrival order != batch order
            app._apply_episode("iter 1/3 · dev", {
                "index": idx, "total": 3, "seed": idx, "character": "val-dwa-law-fem",
                "progress": 0.1 * idx, "status": "completed", "turns": 1, "depth": 1})
        await pilot.pause()

        assert [r["index"] for r in _rows_in_order(app._cur_rows_by_index)] == [0, 1, 2]
        assert app._eval_step is not None
        done, total, mean = app._eval_step
        assert (done, total) == (3, 3)          # completed-count, not arrival index
        assert round(mean, 3) == 0.1             # mean(0.0, 0.1, 0.2)


async def test_display_handler_exception_is_dropped_not_propagated():
    app = EvolveApp(CFG, run=None)
    async with app.run_test():
        app._apply_episode("iter 1/3 · dev", {})    # missing keys -> would raise; must be swallowed
        app._apply_state({})                          # missing keys -> swallowed
        app._apply_log("iter 1/3", "not even json")   # swallowed
        # app still works afterward:
        app._apply_state({"phase": "mutating", "iteration": 1, "baseline_dev": 0.07,
                          "baseline_held": 0.05, "best_dev": 0.09, "best_held": 0.06,
                          "wins": 1, "tokens": 0, "detail": ""})
        assert "MUTATING iter 1/3" in str(app.query_one("#status").content)


async def test_worker_exception_is_captured_not_wrapped_in_workerfailed():
    """A failure outside run_loop's per-iteration try/except (e.g. cold-start
    with the hub or docker down) propagates out of `_run`. It must land on
    `app.error` as the ORIGINAL exception -- not escape `app.run()` wrapped in
    Textual's `WorkerFailed`, which would defeat main()'s httpx/docker
    exception handlers in cli.py (see the `evolve` branch there)."""
    def boom(callbacks):
        raise RuntimeError("cold start: hub unreachable")

    app = EvolveApp(CFG, run=boom)
    async with app.run_test():
        # Poll for the side effect itself rather than `workers.wait_for_complete()`:
        # `_worker`'s except clause calls `self.exit()` on itself, which races the
        # app's own shutdown-triggered `worker.cancel()` against the worker thread
        # returning -- that can leave the Worker's own bookkeeping state CANCELLED
        # (wait_for_complete then raises WorkerCancelled) even though `self.error`
        # was already set correctly beforehand. The attribute assignment is what
        # this test is verifying, so wait on that directly.
        for _ in range(200):  # up to ~2s
            if app.error is not None:
                break
            await asyncio.sleep(0.01)
        assert isinstance(app.error, RuntimeError)
        assert str(app.error) == "cold start: hub unreachable"
        assert app.results is None  # never assigned -- the run= call raised
