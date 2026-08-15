"""Widget-level tests for ``NetHackersApp`` -- the dashboard shell (Task
10): a ``.tabbar`` header showing identity + hub over a ``ContentSwitcher``
hosting the five section views, switched by number-key bindings, plus an
optional pushed ``EvolveScreen`` whose ``.error``/``.results`` this shell
delegates through its own ``.error``/``.results`` properties (what
``cli.py``'s evolve TTY branch reads after ``app.run()``).

Mounted against a dead loopback hub (nothing listens on port 1, so every
hub-backed section view's real httpx call fails near-instantly with
``ConnectError`` and degrades to its own friendly "unreachable" message --
the same hermeticity trick ``test_tui_home.py``/``test_tui_hub_views.py``
already use) rather than a plausible real address like
``http://localhost:8000``: a real hub dev server can genuinely be listening
there on a developer's machine (e.g. via ``docker compose up``), and this
suite must never depend on -- or accidentally talk to -- one.
"""
from __future__ import annotations

import asyncio
import threading

from textual.widgets import ContentSwitcher, Input, Tabs

from nethackers.hubclient.credentials import Credentials
from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.status import EvolveConfig

_DEAD_HUB = "http://127.0.0.1:1"


async def test_shell_shows_identity_and_switches_sections():
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        assert "@castiel" in str(app.query_one(".idbar").render())
        assert app.query_one("#body", ContentSwitcher).current == "home"  # default start

        await pilot.press("2")  # -> boards
        await pilot.pause()
        assert app.query_one("#body", ContentSwitcher).current == "boards"


async def test_shell_guest_when_logged_out():
    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test():
        text = str(app.query_one(".idbar").render())
        assert "guest" in text
        assert "@" not in text  # no stray "@"/"@None" when nobody is logged in


async def test_escape_leaves_a_focused_field_so_q_can_quit():
    """A focused text ``Input`` swallows letters, so the advertised ``q`` quit
    is dead while you're typing in the Evolve form's objective filter (Textual
    ``Input`` consumes printable keys before any binding, ``priority`` or not).
    ``escape`` hands control back to the modal nav (navigate mode, nothing
    focused), restoring the global ``q`` / ``1–6`` keys -- the way out of the
    field a stuck user needs."""
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        await pilot.press("6")  # -> Evolve
        await pilot.pause()
        app.query_one("#f_obj_filter", Input).focus()
        await pilot.pause()
        assert isinstance(app.focused, Input)  # in a text field, `q` would type

        await pilot.press("escape")
        await pilot.pause()
        # back in navigate mode: nothing focused (so q/1-6 fire), cursor on a tab
        assert app.focused is None
        assert app._nav_cursor is not None and (app._nav_cursor.id or "").startswith("tab-")

        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running  # `q` quits again


async def test_activating_a_tab_externally_syncs_the_keyboard_cursor():
    # regression: a mouse click on a tab (or Textual Tabs' own ←/→ when #nav
    # holds focus) switched the active section but left the gold cursor on the
    # old tab -- two tabs looked highlighted. The cursor must follow the active.
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"), start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()  # _nav_start -> cursor on tab-runs
        app.query_one("#nav", Tabs).active = "tab-map"  # simulate an external activation
        await pilot.pause()
        assert app._nav_cursor is not None and app._nav_cursor.id == "tab-map"
        golds = [t.id for t in app.screen.query("#nav Tab.-cursor")]
        assert golds == ["tab-map"]  # exactly one highlighted tab, matching the section


async def test_home_grid_is_arrow_navigable():
    """The modal 2D navigator starts on the Home tab holding no real focus
    (so nothing can eat a keystroke); Down dives into the top-left card and
    the arrows walk the 2x2 grid, with Up from the top row returning to the
    section tab."""
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        await pilot.pause()  # _nav_start runs after the first refresh
        assert app.focused is None  # navigate mode: no real focus
        assert app._nav_cursor is not None and app._nav_cursor.id == "tab-home"

        await pilot.press("down")  # dive into the grid's first (top-left) card
        await pilot.pause()
        assert app._nav_cursor.id == "home_yours"
        await pilot.press("right")
        await pilot.pause()
        assert app._nav_cursor.id == "home_board"  # top-right
        await pilot.press("down")
        await pilot.pause()
        assert app._nav_cursor.id == "home_attain"  # bottom-right
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert app._nav_cursor.id == "tab-home"  # up out of the grid, back to the tab


# --- .error/.results delegate to the pushed EvolveScreen -------------------
#
# cli.py's evolve TTY branch reads `app.error`/`app.results` straight off
# NetHackersApp after `app.run()` (mirroring what it used to read off the
# old EvolveApp directly) -- these pin that delegation actually works, both
# when there's no evolve run in flight and when one succeeds/fails.


async def test_error_and_results_are_none_without_an_evolve_run():
    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test():
        assert app.error is None
        assert app.results is None


class _Plan:
    """Minimal ``EvolvePlan`` stand-in: ``start_run`` reads ``rid``/``cfg`` and
    drives ``run`` on the background worker."""

    def __init__(self, run) -> None:
        self.rid = "r1"
        self.cfg = EvolveConfig(objective="val-dwa-law-fem", backend="claude", iterations=1)
        self.run = run


async def test_results_reflect_a_finished_background_run():
    def fake_run(callbacks):
        return {"ok": True}

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, evolve=_Plan(fake_run))
    async with app.run_test():
        for _ in range(200):  # up to ~2s
            if app.results is not None:
                break
            await asyncio.sleep(0.01)
        assert app.results == {"ok": True}
        assert app.error is None


async def test_error_reflects_a_failed_background_run_as_the_original_exception():
    def boom(callbacks):
        raise RuntimeError("cold start: hub unreachable")

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, evolve=_Plan(boom))
    async with app.run_test():
        for _ in range(200):  # up to ~2s
            if app.error is not None:
                break
            await asyncio.sleep(0.01)
        assert isinstance(app.error, RuntimeError)
        assert str(app.error) == "cold start: hub unreachable"
        assert app.results is None


async def test_run_survives_leaving_the_monitor_and_can_be_reopened_and_stopped():
    """The jump-in/out contract: start_run registers the run + opens its
    monitor; esc detaches to the dashboard WITHOUT stopping it; the run keeps
    running; it can be reopened; stop_run stops it."""
    fired = threading.Event()

    def long_run(callbacks):
        callbacks["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 0})
        fired.set()
        callbacks["stop"].wait(timeout=3)  # block like a real run until stopped
        return []

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app.start_run(_Plan(long_run))
        await pilot.pause()
        for _ in range(200):  # wait for the worker to fire on_state
            if fired.is_set():
                break
            await asyncio.sleep(0.01)
        await pilot.pause()

        assert run.rid in app._runs                      # registered
        assert isinstance(app.screen, RunMonitor) and app.screen.run is run  # monitor opened

        await pilot.press("escape")                      # leave the monitor
        await pilot.pause()
        assert not isinstance(app.screen, RunMonitor)    # detached to the dashboard
        assert run.running and not run.stop.is_set()     # ... but the run keeps going

        app.open_run(run.rid)                            # jump back in
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor) and app.screen.run is run

        app.stop_run(run.rid)                            # explicit stop
        for _ in range(200):
            if not run.running:
                break
            await asyncio.sleep(0.01)
        assert run.stop.is_set() and run.status == "stopped"


async def test_starting_a_second_run_swaps_the_monitor_instead_of_stacking():
    # regression: start_run/open_run always push_screen'd, so two runs stacked
    # two monitors -- esc walked back through stale monitors instead of the
    # dashboard, and opening a run could surface the previous run's monitor.
    def long_run(callbacks):
        callbacks["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 1})
        callbacks["stop"].wait(timeout=3)
        return []

    def monitors(app):
        return [s for s in app.screen_stack if isinstance(s, RunMonitor)]

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        pa = _Plan(long_run)
        pa.rid = "run-A"
        pb = _Plan(long_run)
        pb.rid = "run-B"
        ra = app.start_run(pa)
        await pilot.pause()
        rb = app.start_run(pb)  # swaps A's monitor for B's, never stacks
        await pilot.pause()
        assert len(monitors(app)) == 1 and monitors(app)[0].run is rb

        await pilot.press("escape")  # a SINGLE esc returns to the dashboard
        await pilot.pause()
        assert not isinstance(app.screen, RunMonitor)

        app.open_run("run-A")  # opening A shows A (not a buried monitor)
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor) and app.screen.run is ra
        app.open_run("run-B")  # monitor -> monitor swaps straight to B
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor) and app.screen.run is rb
        assert len(monitors(app)) == 1

        app.stop_run("run-A")
        app.stop_run("run-B")
        for _ in range(200):
            if not (ra.running or rb.running):
                break
            await asyncio.sleep(0.01)
