"""Screen-level smoke tests for EvolveScreen -- the live evolution monitor
(PARENT -> CANDIDATE -> eval -> lineage motif). Mirrors test_tui_app.py's
approach (drive the guarded handlers directly through a host App +
run_test()/pilot) but targets the new per-motif Static layout
(#parent/#candidate/#eval/#lineage/#ledger/#statusline) instead of the old
single #status bar, and additionally exercises the real worker/
call_from_thread path -- adapted for a Screen, which (unlike App) has
neither .call_from_thread() nor .exit() of its own, only self.app does."""
from __future__ import annotations

import asyncio

from textual.app import App

from nethackers.tui.screens.evolve import EvolveScreen
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig("wiz-elf-cha-mal", "claude", 3, 200_000)


def _state(phase="mutating", **kw):
    base = {"phase": phase, "iteration": 2, "baseline_dev": 0.30, "baseline_held": 0.28,
            "best_dev": 0.44, "best_held": 0.41, "wins": 1, "tokens": 0, "detail": "",
            "parent_digest": "7a3f", "parent_dev": 0.30, "parent_held": 0.28,
            "generation": 1}
    base.update(kw)
    return base


class _Host(App):
    def __init__(self, run=None, exit_on_error=False):
        super().__init__()
        self.screen_ = EvolveScreen(CFG, run=run, exit_on_error=exit_on_error)

    def on_mount(self) -> None:
        self.push_screen(self.screen_)


async def test_parent_panel_renders_and_survives_bad_payloads():
    host = _Host()
    async with host.run_test() as pilot:
        host.screen_._apply_state(_state())
        await pilot.pause()
        parent_text = str(host.screen_.query_one("#parent").render())
        assert "PARENT" in parent_text
        assert "#7a3f" in parent_text

        # Bad/malformed payloads are swallowed (guarded handlers) -- the
        # screen must survive and keep working afterward.
        host.screen_._apply_episode("iter 2/3 · dev", {})
        host.screen_._apply_state({})
        await pilot.pause()

        host.screen_._apply_state(_state(phase="registered"))
        await pilot.pause()
        ledger_text = str(host.screen_.query_one("#ledger").render())
        assert "2 ✓ registered" in ledger_text


async def test_rejected_transition_renders_tombstone_and_ledger_entry():
    host = _Host()
    async with host.run_test() as pilot:
        host.screen_._apply_state(_state(phase="mutating"))
        host.screen_._apply_state(_state(phase="rejected", detail="no dev gain"))
        await pilot.pause()

        candidate_text = str(host.screen_.query_one("#candidate").render())
        assert "RIP" in candidate_text
        ledger_text = str(host.screen_.query_one("#ledger").render())
        assert "2 ✗ no dev gain" in ledger_text


async def test_registered_then_rejected_sequence_builds_ledger_and_lineage():
    """A full mutating -> registered -> mutating -> rejected sequence (two
    iterations, a changing parent_digest) must not crash. Both ledger rows
    must accumulate and the lineage chain must grow with the new parent."""
    host = _Host()
    async with host.run_test() as pilot:
        host.screen_._apply_state(_state(phase="mutating", iteration=1))
        host.screen_._apply_state(_state(phase="registered", iteration=1))
        host.screen_._apply_state(_state(phase="mutating", iteration=2, parent_digest="c0de"))
        host.screen_._apply_state(_state(phase="rejected", iteration=2, detail="no held-out gain"))
        await pilot.pause()

        ledger_text = str(host.screen_.query_one("#ledger").render())
        assert "1 ✓ registered" in ledger_text
        assert "2 ✗ no held-out gain" in ledger_text
        lineage_text = str(host.screen_.query_one("#lineage").render())
        assert "#7a3f" in lineage_text and "#c0de" in lineage_text


async def test_bad_log_payload_is_swallowed_and_screen_keeps_working():
    host = _Host()
    async with host.run_test() as pilot:
        host.screen_._apply_state(_state(phase="mutating", iteration=1))
        host.screen_._apply_log("iter 1/3", "not even json")  # unparseable -- must not raise
        await pilot.pause()
        host.screen_._apply_state(_state(phase="mutating", iteration=1))
        await pilot.pause()
        assert "PARENT" in str(host.screen_.query_one("#parent").render())


async def test_episode_mounts_table_and_tracks_per_batch_counts():
    host = _Host()
    async with host.run_test() as pilot:
        host.screen_._apply_state(_state(phase="evaluating-dev", iteration=1))
        host.screen_._apply_episode("iter 1/3 · dev", {
            "index": 0, "total": 2, "seed": 0, "character": "wiz-elf-cha-mal",
            "progress": 0.2, "status": "died", "turns": 5, "depth": 1})
        host.screen_._apply_episode("iter 1/3 · dev", {
            "index": 1, "total": 2, "seed": 1, "character": "wiz-elf-cha-mal",
            "progress": 0.6, "status": "ascended", "turns": 9, "depth": 3})
        await pilot.pause()

        assert host.screen_.query("#tables Static")
        assert host.screen_._counts == {"died": 1, "ascended": 1}
        eval_text = str(host.screen_.query_one("#eval").render())
        assert "eval · dev" in eval_text and "2/2" in eval_text


async def test_worker_runs_end_to_end_via_call_from_thread():
    """Exercises the real @work(thread=True) path (not just calling handlers
    directly): proves the Screen-specific self.app.call_from_thread /
    self.app.exit adaptation actually works off-thread (Screen itself has
    neither method -- only App does)."""
    def fake_run(callbacks):
        callbacks["on_state"](_state(phase="mutating", iteration=1))
        callbacks["on_episode"]("iter 1/3 · dev", {
            "index": 0, "total": 1, "seed": 0, "character": "wiz-elf-cha-mal",
            "progress": 0.5, "status": "completed", "turns": 3, "depth": 2})
        callbacks["on_log"]("iter 1/3", "not even json")
        return {"ok": True}

    host = _Host(run=fake_run)
    async with host.run_test():
        for _ in range(200):  # up to ~2s
            if host.screen_.results is not None:
                break
            await asyncio.sleep(0.01)
        assert host.screen_.results == {"ok": True}
        assert host.screen_.error is None
        assert "PARENT" in str(host.screen_.query_one("#parent").render())


async def test_worker_exception_is_captured_not_wrapped_in_workerfailed():
    """Mirrors test_tui_app.py's WorkerFailed-avoidance test for EvolveApp,
    adapted for the Screen: the worker's except clause must set self.error to
    the ORIGINAL exception and end the app via self.app.exit() (Screen has no
    .exit() of its own). This is the CLI contract (NetHackersApp's evolve=
    construction path passes exit_on_error=True so cli.py can re-raise
    app.error after app.run() returns) -- see the in-app counterpart below
    for the exit_on_error=False (EvolveForm) disposition."""
    def boom(callbacks):
        raise RuntimeError("cold start: hub unreachable")

    host = _Host(run=boom, exit_on_error=True)
    async with host.run_test():
        for _ in range(200):  # up to ~2s
            if host.screen_.error is not None:
                break
            await asyncio.sleep(0.01)
        assert isinstance(host.screen_.error, RuntimeError)
        assert str(host.screen_.error) == "cold start: hub unreachable"
        assert host.screen_.results is None


async def test_in_app_worker_exception_notifies_and_dismisses_without_exiting_the_app():
    """The in-app `⚔ Evolve` launch path (EvolveForm pushes EvolveScreen with
    the default exit_on_error=False) must NOT tear down the whole dashboard
    on a cold-start failure -- only the CLI path (exit_on_error=True, see the
    test above) does that. Instead the worker's exception handler surfaces
    the error via App.notify and dismisses the screen back to whatever's
    beneath it, leaving the rest of the app running."""
    def boom(callbacks):
        raise RuntimeError("docker: cannot connect to the Docker daemon")

    host = _Host(run=boom, exit_on_error=False)
    notify_calls: list[tuple[str, dict]] = []
    exit_calls: list[tuple[tuple, dict]] = []
    host.notify = lambda message, **kw: notify_calls.append((message, kw))
    host.exit = lambda *a, **kw: exit_calls.append((a, kw))

    async with host.run_test() as pilot:
        for _ in range(200):  # up to ~2s
            if host.screen_.error is not None:
                break
            await asyncio.sleep(0.01)
        await pilot.pause()

        # original exception captured, exactly as the CLI path captures it
        assert isinstance(host.screen_.error, RuntimeError)
        assert str(host.screen_.error) == "docker: cannot connect to the Docker daemon"
        assert host.screen_.results is None

        # surfaced via a notification, not a silent app teardown
        assert notify_calls, "expected app.notify to be called with the error"
        message, kwargs = notify_calls[-1]
        assert "docker: cannot connect to the Docker daemon" in message
        assert kwargs.get("severity") == "error"

        # dismissed back to the dashboard, and the app itself was NOT exited
        assert host.screen_ not in host.screen_stack
        assert exit_calls == []
