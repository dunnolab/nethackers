"""Tests for the redesigned Home view: the identity-and-standing hero.

The Home landing screen no longer hosts the old score panels
(``your_solutions_panel``/``leaderboard_panel``/``attainment_panel``) -- those
boards moved to their own tabs. What stays here is ``recent_runs_panel`` (the
Runs tab reuses it) plus ``HomeView`` itself: an ASCII wordmark, a tagline, the
``@``-hero identity block, a login/logout button, and -- once logged in -- a
NetHack-style status line.

``HomeView`` is exercised against a real (but instantly connection-refused)
loopback URL so ``_refresh``'s ``_registered_count`` try/except is proven to
degrade to a friendly ``—`` instead of crashing -- no network mocking needed,
since a closed local port fails immediately. The pure helpers
(``_identity_text``/``_registered_count``) are unit-tested directly.
"""
from __future__ import annotations

import io

from rich.console import Console
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from nethackers.tui.art import NETHACKERS_BANNER
from nethackers.tui.screens.home import (
    HomeView,
    _identity_text,
    _registered_count,
    recent_runs_panel,
)

# An address nothing listens on: httpx.ConnectError fires near-instantly
# (loopback, no DNS), so the smoke test never depends on real network access.
_DEAD_HUB = "http://127.0.0.1:1"


def _p(renderable) -> str:
    buf = io.StringIO()
    Console(width=90, file=buf).print(renderable)
    return buf.getvalue()


# --- recent_runs_panel: objective + a distinguishable win/iteration ratio -


def test_recent_runs_panel_shows_objective_and_win_fraction():
    runs = [
        {"run_id": "r-1", "objective": "wiz-elf-cha-mal", "operator": "claude",
         "wins": 2, "iterations": 5, "best_dev": 0.44},
    ]
    out = _p(recent_runs_panel(runs))
    assert "wiz-elf-cha-mal" in out
    assert "2/5" in out  # wins/iterations, not just a lone digit that could match anything
    assert "held" not in out  # validation/held-out is gone -- no dead column


# --- _identity_text: logged-out prompt vs. the @login hero -----------------


def test_identity_text_logged_out_prompts_to_log_in():
    out = _identity_text(None)
    assert "not logged in" in out
    assert "@sam" not in out  # no stray login rendered when nobody is logged in


def test_identity_text_shows_the_login_when_present():
    out = _identity_text("sam")
    assert "@sam" in out
    assert "logged in via GitHub" in out


# --- _registered_count: counts search rows, None when the hub is down ------


class _FakeClient:
    """Duck-types HubClient's ``search()`` -- no real HubClient or network."""

    def __init__(self, rows=None, boom=False):
        self._rows = rows
        self._boom = boom

    def search(self, owner):
        if self._boom:
            raise RuntimeError("hub unreachable")
        return self._rows


def test_registered_count_counts_the_search_rows():
    client = _FakeClient(rows=[{"digest": "a"}, {"digest": "b"}, {"digest": "c"}])
    assert _registered_count(client, "castiel") == 3


def test_registered_count_counts_zero_when_search_is_empty():
    assert _registered_count(_FakeClient(rows=[]), "castiel") == 0


def test_registered_count_is_none_when_search_raises():
    # a raising hub degrades to None (Home renders "—", not a misleading 0)
    assert _registered_count(_FakeClient(boom=True), "castiel") is None


# --- HomeView: widget smoke tests ------------------------------------------


class _Host(App):
    def __init__(self, login: str | None):
        super().__init__()
        self._login = login

    def compose(self) -> ComposeResult:
        yield HomeView(_DEAD_HUB, self._login, id="home")


async def test_home_view_logged_out_renders_banner_and_login_button(monkeypatch, tmp_path):
    # keep the standing read hermetic (never touch the user's real runs dir)
    monkeypatch.setenv("NETHACKERS_DATA_ROOT", str(tmp_path))
    host = _Host(None)
    async with host.run_test() as pilot:
        await pilot.pause()
        banner = str(host.query_one("#home_banner", Static).render())
        assert NETHACKERS_BANNER.splitlines()[-1] in banner  # the wordmark rendered

        btn = host.query_one("#home_auth", Button)
        assert str(btn.label) == "Log in with GitHub"

        # logged out -> the NetHack status line is hidden, and no crash from the dead hub
        assert host.query_one("#home_status", Static).display is False


async def test_home_view_logged_in_renders_status_line(monkeypatch, tmp_path):
    monkeypatch.setenv("NETHACKERS_DATA_ROOT", str(tmp_path))
    host = _Host("castiel")
    async with host.run_test() as pilot:
        await pilot.pause()
        status = host.query_one("#home_status", Static)
        assert status.display is True  # logged in -> the standing line is shown
        # dead hub -> programs is None -> rendered as "—", not a crash
        assert "Programs:" in str(status.render())

        btn = host.query_one("#home_auth", Button)
        assert str(btn.label) == "Log out"
