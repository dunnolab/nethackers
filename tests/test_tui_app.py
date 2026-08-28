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


# Every NETHACKERS_* key load_stage() reads -- cleared below, same isolation
# tests/test_config.py's test_prod_flag_bypasses_discovery and
# tests/test_cli_login.py's test_whoami_respects_o_json already use -- so an
# ambient shell/worktree .env.stack/env can never leak into the idbar's own
# ``self._stage = load_stage()`` seam (``NetHackersApp.__init__``).
_STAGE_ENV_KEYS = (
    "NETHACKERS_STAGE", "NETHACKERS_STAGE_FILE", "NETHACKERS_HUB", "NETHACKERS_HUB_PORT",
    "COMPOSE_PROJECT_NAME", "NETHACKERS_DATA_ROOT", "NETHACKERS_REPO_NAME",
    "NETHACKERS_ARENA_IMAGE", "NETHACKERS_MUTATOR_IMAGE", "NETHACKERS_CLIENT_ID",
)


async def test_idbar_shows_stage_tag_for_non_prod_and_omits_it_for_prod(monkeypatch, tmp_path):
    # NetHackersApp.__init__ resolves its stage via a bare load_stage() --
    # the same ambient-discovery seam cli._run's own startup line reads --
    # so construction must be hermetic here too: an empty tmp_path cwd plus
    # every NETHACKERS_* key cleared.
    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    prod_app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with prod_app.run_test():
        text = str(prod_app.query_one(".idbar").render())
        assert "stage:" not in text  # prod stays silent -- no tag at all

    monkeypatch.setenv("NETHACKERS_STAGE", "wt")
    wt_app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with wt_app.run_test():
        text = str(wt_app.query_one(".idbar").render())
        assert "· stage:wt" in text  # a named stage gets its own idbar suffix


# --- idbar effective identity: who you are TO THE HUB you're pointed at,
# not just your local login state (mirrors cli.py's `whoami`). `HubClient` is
# monkeypatched on the `tui.app` module so the offline/github branches are
# deterministic; the unreachable case reuses `_DEAD_HUB` for real, like every
# other idbar test in this file. The fetch runs on a background worker (the
# same off-thread + call_from_thread pattern `HomeView._fetch_programs`
# uses) so it never blocks the dashboard's first paint.


def _fake_hub_client_cls(mode: str):
    class _Fake:
        def __init__(self, base_url, *, timeout=None):
            pass

        def hub_mode(self):
            return mode
    return _Fake


async def test_idbar_shows_offline_in_caps_when_logged_out_against_an_offline_hub(monkeypatch):
    from nethackers.tui import app as app_module
    monkeypatch.setattr(app_module, "HubClient", _fake_hub_client_cls("offline"))

    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test():
        for _ in range(200):
            if app._hub_mode is not None:
                break
            await asyncio.sleep(0.01)
        text = str(app.query_one(".idbar").render())
        assert "OFFLINE" in text


async def test_idbar_shows_mismatch_warning_when_logged_in_against_an_offline_hub(monkeypatch):
    # The motivating bug: @vkurenkov logged in locally, pointed at a local
    # offline/stub hub -- the idbar must say the hub won't accept it, not
    # just echo the local login back as if it would work there.
    from nethackers.tui import app as app_module
    monkeypatch.setattr(app_module, "HubClient", _fake_hub_client_cls("offline"))

    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("vkurenkov", "sekrit-tok"))
    async with app.run_test():
        for _ in range(200):
            if app._hub_mode is not None:
                break
            await asyncio.sleep(0.01)
        text = str(app.query_one(".idbar").render())
        assert "@vkurenkov" in text
        assert "OFFLINE" in text
        assert "sekrit-tok" not in text  # the token itself never leaks


async def test_idbar_stays_plain_for_a_github_hub(monkeypatch):
    from nethackers.tui import app as app_module
    monkeypatch.setattr(app_module, "HubClient", _fake_hub_client_cls("github"))

    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("vkurenkov", "t"))
    async with app.run_test():
        for _ in range(200):
            if app._hub_mode is not None:
                break
            await asyncio.sleep(0.01)
        text = str(app.query_one(".idbar").render())
        assert "@vkurenkov" in text
        assert "OFFLINE" not in text


async def test_idbar_marks_an_unreachable_hub_without_losing_the_baseline_identity():
    # A genuinely dead hub: the async fetch must never crash the app, and the
    # eventual unreachable marker is APPENDED, never replacing the baseline
    # "@login"/"guest" text -- so it can't race the earlier, baseline-only
    # idbar assertions in this same file (test_shell_shows_identity_and_...,
    # test_shell_guest_when_logged_out) into flakiness, whichever finishes
    # first.
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("vkurenkov", "t"))
    async with app.run_test():
        for _ in range(200):
            if app._hub_unreachable:
                break
            await asyncio.sleep(0.01)
        text = str(app.query_one(".idbar").render())
        assert "@vkurenkov" in text
        assert "unreachable" in text


async def test_escape_leaves_a_focused_field_so_q_can_quit():
    """A focused text ``Input`` swallows letters, so the advertised ``q`` quit
    is dead while you're typing in one of the Evolve form's text fields (e.g.
    iterations; Textual ``Input`` consumes printable keys before any binding,
    ``priority`` or not). ``escape`` hands control back to the modal nav
    (navigate mode, nothing focused), restoring the global ``q`` / ``1–6``
    keys -- the way out of the field a stuck user needs."""
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        await pilot.press("6")  # -> Evolve
        await pilot.pause()
        app.query_one("#f_iters", Input).focus()
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


async def test_evolve_select_opens_on_one_enter_not_two():
    """Navigating the modal cursor onto a Select must NOT open it (↑↓ just move
    on); a single enter both focuses AND opens its list -- the fix for the
    'enter twice to open the operator' friction."""
    from textual.widgets import Select

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, start="evolve")
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        await pilot.press("down")            # dive into the form (navigate mode)
        await pilot.pause()
        op = app.query_one("#f_op", Select)
        app._nav_set_cursor(op)              # cursor onto the operator Select
        assert app._nav_mode == "navigate"   # still navigating...
        assert not op.expanded               # ...and navigating never opened the list
        await pilot.press("enter")           # ONE enter opens it
        await pilot.pause()
        assert op.expanded


async def test_closed_select_does_not_reopen_on_arrows():
    """After you pick from a Select it closes but stays focused; ↑↓ must then
    MOVE ON (back to navigate), not reopen the list."""
    from textual.widgets import Select

    app = NetHackersApp(hub=_DEAD_HUB, creds=None, start="evolve")
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        await pilot.press("down")            # dive into the form
        await pilot.pause()
        op = app.query_one("#f_op", Select)
        app._nav_set_cursor(op)
        await pilot.press("enter")           # open the list
        await pilot.pause()
        assert op.expanded
        await pilot.press("enter")           # pick the highlighted value -> closes, keeps focus
        await pilot.pause()
        assert not op.expanded
        await pilot.press("down")            # must NOT reopen -> navigate on
        await pilot.pause()
        assert not op.expanded               # stayed closed
        assert app._nav_mode == "navigate"   # returned to navigate mode


async def test_navigate_recovers_when_the_cursor_element_vanished():
    # regression: opening a run from the Runs list put the cursor on its button;
    # stopping the run removed that button, so returning to navigate mode re-added
    # the highlight to a gone widget -> 0 visible cursors. Recover to the section tab.
    from textual.widgets import Static

    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"), start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._nav_cursor = Static(id="gone")  # a never-mounted (vanished) element
        app._nav_to_navigate()
        await pilot.pause()
        assert len(app.screen.query(".-cursor")) == 1  # exactly one, not zero
        assert (app._nav_cursor.id or "").startswith("tab-")  # fell back to the section tab


async def test_leaving_a_run_monitor_reclaims_navigate_mode():
    # regression: returning to the dashboard from a monitor left #nav focused
    # (Textual restores focus on screen-resume), so its Tabs ate ←/→ and the
    # cursor desynced -- the "top tabs after quitting the evolve monitor" bug.
    fired = threading.Event()

    def long_run(callbacks):
        callbacks["on_state"]({
            "phase": "mutating", "iteration": 1, "baseline_dev": 0.0, "baseline_held": 0.0,
            "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
            "parent_digest": "seed0", "parent_dev": 0.0, "parent_held": 0.0, "generation": 1})
        fired.set()
        callbacks["stop"].wait(timeout=3)
        return []

    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"), start="runs")
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app.start_run(_Plan(long_run))  # opens the monitor
        for _ in range(200):
            if fired.is_set():
                break
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert isinstance(app.screen, RunMonitor)

        await pilot.press("escape")  # leave the monitor -> dashboard
        await pilot.pause()
        assert not isinstance(app.screen, RunMonitor)
        assert app.focused is None                             # navigate mode reclaimed
        assert len(app.screen.query(".-cursor")) == 1          # exactly one gold cursor
        before = app._nav_cursor.id
        await pilot.press("right")                             # arrows move ONE tab...
        await pilot.pause()
        after = app._nav_cursor.id
        assert before != after
        assert app.query_one("#nav", Tabs).active == after     # ...and stay in sync

        app.stop_run(run.rid)
        for _ in range(200):
            if not run.running:
                break
            await asyncio.sleep(0.01)


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


async def test_frontier_up_from_body_returns_to_active_subtab_not_the_other():
    # regression: up from the Frontier grid landed on the geometrically-nearest
    # subtab (ft-program), silently flipping the regime Universe->Program --
    # the "rejump tabs by one" glitch. It must return to the ACTIVE subtab.
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"), start="home")
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.press("3")     # -> Frontier
        await pilot.press("down")  # onto the Universe subtab
        await pilot.pause()
        assert app._nav_cursor is not None and app._nav_cursor.id == "ft-universe"
        await pilot.press("down")  # into the grid body
        await pilot.pause()
        assert app._nav_cursor is not None
        assert not (app._nav_cursor.id or "").startswith("ft-")
        await pilot.press("up")    # back up -> the ACTIVE subtab, regime unchanged
        await pilot.pause()
        assert app._nav_cursor.id == "ft-universe"
        assert app.query_one("#ftabs", Tabs).active == "ft-universe"


async def test_home_is_arrow_navigable():
    """The modal 2D navigator starts on the Home tab holding no real focus (so
    nothing can eat a keystroke); Down dives into the Home card's single control
    -- the login/logout button -- and Up returns to the section tab."""
    app = NetHackersApp(hub=_DEAD_HUB, creds=Credentials("castiel", "t"))
    async with app.run_test() as pilot:
        await pilot.pause()  # _nav_start runs after the first refresh
        assert app.focused is None  # navigate mode: no real focus
        assert app._nav_cursor is not None and app._nav_cursor.id == "tab-home"

        await pilot.press("down")  # dive into the card's one focusable control
        await pilot.pause()
        assert app._nav_cursor.id == "home_auth"  # the login/logout button
        await pilot.press("up")
        await pilot.pause()
        assert app._nav_cursor.id == "tab-home"  # up out of the card, back to the tab


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
