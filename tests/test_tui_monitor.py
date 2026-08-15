"""Screen-level tests for RunMonitor -- the re-openable view onto a Run:
backfill from a pre-populated Run, live render forwarded by the host, esc
detaches WITHOUT stopping the run, `s` stops it. (The worker->Run reductions
themselves are covered by test_tui_run.py.)"""
from __future__ import annotations

from textual.app import App

from nethackers.tui.run import Run
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig("wiz-elf-cha-mal", "claude", 3)


def _state(phase="mutating", **kw):
    base = {"phase": phase, "iteration": 2, "baseline_dev": 0.30, "baseline_held": 0.28,
            "best_dev": 0.44, "best_held": 0.41, "wins": 1, "tokens": 0, "detail": "",
            "parent_digest": "7a3f", "parent_dev": 0.30, "parent_held": 0.28, "generation": 1}
    base.update(kw)
    return base


def _populated_run() -> Run:
    r = Run("run-1", CFG)
    r.apply_state(_state("mutating", iteration=1, parent_digest="seed0"))
    r.apply_episode("iter 1/3 · dev", {
        "index": 0, "total": 2, "seed": 0, "character": "wiz-elf-cha-mal",
        "progress": 0.2, "status": "died", "turns": 5, "depth": 1})
    r.apply_episode("iter 1/3 · dev", {
        "index": 1, "total": 2, "seed": 1, "character": "wiz-elf-cha-mal",
        "progress": 0.6, "status": "ascended", "turns": 9, "depth": 3})
    r.apply_log("iter 1/3",
                '{"type":"assistant","message":{"content":[{"type":"text","text":"editing"}]}}')
    r.apply_state(_state("registered", iteration=1, parent_digest="elite1"))
    return r


class _Host(App):
    def __init__(self, run: Run) -> None:
        super().__init__()
        self._run = run

    def on_mount(self) -> None:
        self.push_screen(RunMonitor(self._run))


async def test_backfill_renders_the_runs_accumulated_state():
    host = _Host(_populated_run())
    async with host.run_test() as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        assert "PARENT" in str(mon.query_one("#parent").render())
        assert mon.query("#tables Static")  # the batch table was backfilled
        assert "1 ✓ registered" in str(mon.query_one("#ledger").render())
        assert "#seed" in str(mon.query_one("#lineage").render())  # seed0 -> #seed


async def test_live_render_mounts_a_new_batch_table():
    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        before = len(mon.query("#tables Static"))
        run.apply_episode("iter 2/3 · dev", {"index": 0, "total": 1, "seed": 0,
            "character": "wiz-elf-cha-mal", "progress": 0.3, "status": "completed",
            "turns": 2, "depth": 1})
        mon.render_episode("iter 2/3 · dev", {})
        await pilot.pause()
        assert len(mon.query("#tables Static")) == before + 1  # a new batch table mounted


async def test_escape_detaches_without_stopping_the_run():
    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert isinstance(host.screen, RunMonitor)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(host.screen, RunMonitor)  # detached to whatever's beneath
        assert not run.stop.is_set()                    # the run keeps running


async def test_s_stops_the_run():
    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert run.stop.is_set()  # explicit stop
