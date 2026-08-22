"""Screen-level tests for RunMonitor -- the re-openable view onto a Run:
backfill from a pre-populated Run, live render forwarded by the host, esc
detaches WITHOUT stopping the run, `s` stops it. (The worker->Run reductions
themselves are covered by test_tui_run.py.)"""
from __future__ import annotations

from unittest.mock import patch

from textual.app import App
from textual.widgets import ListView, RichLog

from nethackers.hubclient.live import episode_table
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
        assert run.current_batch().done is True


async def test_finish_rerenders_final_batch_as_complete():
    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        run.finish(results=[])
        with patch("nethackers.tui.screens.monitor.episode_table",
                   wraps=episode_table) as render_table:
            mon.render_state()
        await pilot.pause()
        assert render_table.call_args.kwargs["done"] is True


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


async def test_c_copies_the_selected_iteration_log_to_the_clipboard():
    run = _populated_run()
    run.logs["iter 1/3"] = [("assistant", "line one"), ("tool", "line two")]
    run.sel_tag = "iter 1/3"
    host = _Host(run)
    captured: dict[str, str] = {}
    async with host.run_test() as pilot:
        await pilot.pause()
        host.copy_to_clipboard = lambda t: captured.__setitem__("t", t)  # type: ignore[method-assign]
        await pilot.press("c")
        await pilot.pause()
        assert captured["t"] == "line one\nline two"


async def test_deferred_callbacks_are_safe_after_the_monitor_is_dismissed():
    # regression: _highlight_current / _nav_start are call_after_refresh'd from
    # on_mount; dismissing the monitor (esc, or switch_screen for another run)
    # before they fire left them querying a gone widget tree -> NoMatches crash.
    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        host.pop_screen()  # dismiss the monitor
        await pilot.pause()
        mon._highlight_current()  # guarded no-ops once unmounted -- must not raise
        mon._nav_start()
        assert not isinstance(host.screen, RunMonitor)


async def test_agent_log_labels_the_iteration_follows_it_and_arrows_swap():
    _CLAUDE = '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}'
    run = Run("run-1", CFG)  # CFG.iterations == 3 -> tags are "iter N/3"
    run.apply_state(_state("mutating", iteration=1))
    run.apply_log("iter 1/3", _CLAUDE % "one")
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # let call_after_refresh(_highlight_current) run
        mon = host.screen
        rl = mon.query_one("#logview", RichLog)
        lv = mon.query_one("#logs_list", ListView)
        assert str(rl.border_title) == "iter 1/3"  # the log panel is labeled...
        assert lv.index == 0                        # ...and its iteration highlighted

        run.apply_state(_state("mutating", iteration=2))  # a new iteration begins
        run.apply_log("iter 2/3", _CLAUDE % "two")
        mon.render_log("iter 2/3")
        await pilot.pause()
        await pilot.pause()
        assert str(rl.border_title) == "iter 2/3"  # the view follows the current one
        assert lv.index == 1

        lv.index = 0  # arrow back to the older iteration
        await pilot.pause()
        assert str(rl.border_title) == "iter 1/3" and run.sel_tag == "iter 1/3"


async def test_scorecard_renders_the_per_identity_breakdown_for_a_set_run():
    # a set objective's state carries identities/parent_means -- the monitor
    # should populate #scorecard with the weakest build (the floor) shown.
    run = Run("run-1", CFG)
    run.apply_state(_state("mutating",
                            identities=["wiz-elf-cha-mal", "wiz-orc-cha-mal"],
                            parent_means={"wiz-elf-cha-mal": 0.4, "wiz-orc-cha-mal": 0.1}))
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        assert "wiz-orc-cha-mal" in str(mon.query_one("#scorecard").render())


async def test_cockpit_title_uses_the_compact_resolved_name_for_a_set_objective():
    # cfg.objective for a form/CLI-built set is a long comma list -- the
    # border title should show selector.resolve's compact name instead
    # (e.g. "set:2:<hash>"), not the raw list.
    cfg = EvolveConfig("wiz-elf-cha-mal,wiz-orc-cha-mal", "claude", 3)
    run = Run("run-1", cfg)
    run.apply_state(_state("mutating", identities=["wiz-elf-cha-mal", "wiz-orc-cha-mal"]))
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        title = str(host.screen.query_one("#cockpit").border_title)
        assert cfg.objective not in title
        assert "(2)" in title


async def test_cockpit_title_falls_back_to_the_raw_objective_on_a_bad_token():
    # resolve() can raise for a token the selector doesn't recognize; the
    # title must never crash the screen over it -- on_mount falls back to
    # the raw cfg.objective string.
    cfg = EvolveConfig("not-a-real-objective", "claude", 3)
    run = Run("run-1", cfg)
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        title = str(host.screen.query_one("#cockpit").border_title)
        assert "not-a-real-objective" in title


async def test_arrows_navigate_tabs_and_panes_then_enter_interacts():
    from textual.widgets import Tab

    run = _populated_run()
    host = _Host(run)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # let call_after_refresh(_nav_start) run
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        assert mon._nav_mode == "navigate" and host.focused is None
        assert isinstance(mon._nav_cursor, Tab)  # cursor starts on a tab
        await pilot.press("down")                # dive into the active pane
        await pilot.pause()
        assert mon._nav_cursor is not None and not isinstance(mon._nav_cursor, Tab)
        await pilot.press("enter")               # interact: the pane takes real focus
        await pilot.pause()
        assert mon._nav_mode == "interact" and host.focused is mon._nav_cursor
        await pilot.press("escape")              # back to navigate, NOT leaving
        await pilot.pause()
        assert mon._nav_mode == "navigate" and isinstance(host.screen, RunMonitor)
