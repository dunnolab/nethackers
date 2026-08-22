"""Textual Home view: the NetHackers landing screen. An ASCII "NETHACKERS"
wordmark over a one-line description, your ``@``-hero identity with a clear
login/logout control, and -- once you're logged in -- your standing rendered as
a NetHack status line (Programs / Runs / Wins / Evolved-tokens).

Score-based boards (leaderboard, frontier, elites) live in their own tabs; Home
is the identity-and-standing hero, so it stays legible even before any scores
exist. ``recent_runs_panel`` stays here because the Runs tab reuses it.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rich import box
from rich.table import Table
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.widgets import Button, Static

from nethackers.hubclient.client import HubClient
from nethackers.tui.art import NETHACKERS_BANNER, platform_status_line
from nethackers.tui.screens.runs import read_runs, run_totals
from nethackers.tui.status import _compact

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

_RUNS_DIR = Path.home() / ".nethackers" / "evolve" / "runs"

_TAGLINE = ("Evolve symbolic NetHack players with coding agents.\n"
            "Register your best — climb the shared hub.")


def recent_runs_panel(runs: list[dict[str, Any]]) -> Table:
    """The most recent local evolve runs (``read_runs``'s per-run summaries):
    run id, objective, operator, a ``wins/iterations`` fraction, and best
    dev/held scores. Capped to the 6 most recent (already newest-first). Kept
    here because the Runs tab reuses it."""
    t = Table(header_style="bold", pad_edge=False, box=box.SIMPLE_HEAVY)
    for c in ("run", "objective", "op", "wins", "dev", "held"):
        t.add_column(c)
    for r in runs[:6]:
        t.add_row(
            str(r["run_id"]), str(r["objective"]), str(r.get("operator", "")),
            f"{r['wins']}/{r['iterations']}",
            f"{(r.get('best_dev') or 0):.2f}", f"{(r.get('best_held') or 0):.2f}",
        )
    return t


def _registered_count(client: HubClient, login: str) -> int | None:
    """How many solutions ``login`` has registered, or ``None`` when the hub is
    unreachable -- so Home shows ``—`` instead of a misleading ``0``."""
    try:
        return len(client.search(login) or [])
    except Exception:
        return None


def _identity_text(login: str | None) -> str:
    """The centered ``@``-hero identity block: the hero glyph, then either your
    login or a logged-out prompt."""
    if login:
        return f"[b #ffd54a]@[/]\n[b]@{login}[/]\n[dim]logged in via GitHub[/]"
    return "[b #ffd54a]@[/]\n[dim]not logged in[/]"


class HomeView(Vertical):
    """The landing hero: wordmark + description + identity + login/logout, and
    your NetHack-style standing line once you're logged in. Refetched on mount
    and every time Home is shown again."""

    DEFAULT_CSS = """
    HomeView { align: center middle; height: 1fr; }
    HomeView #home_card {
        width: 64; height: auto;
        border: heavy #d2a24c; background: #16161c; padding: 1 4;
    }
    HomeView #home_banner { width: 100%; text-align: center; color: #d2a24c; text-style: bold; }
    HomeView #home_tagline { width: 100%; text-align: center; color: #7c745f; margin-top: 1; }
    HomeView #home_identity { width: 100%; text-align: center; margin-top: 1; }
    HomeView #home_status {
        width: 100%; text-align: center; margin-top: 1;
        background: #d2a24c; color: #0b0b0e; text-style: bold;
    }
    HomeView #home_authrow { width: 100%; height: auto; align-horizontal: center; margin-top: 1; }
    HomeView Button#home_auth {
        min-width: 26; background: #16161c; color: #d2a24c; border: heavy #d2a24c;
    }
    """

    def __init__(self, hub: str, login: str | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._login = login

    def compose(self) -> ComposeResult:
        with Vertical(id="home_card"):
            yield Static(NETHACKERS_BANNER, id="home_banner")
            yield Static(_TAGLINE, id="home_tagline")
            yield Static(id="home_identity")
            yield Static(id="home_status")
            with Center(id="home_authrow"):
                yield Button("", id="home_auth")

    def on_mount(self) -> None:
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def set_login(self, login: str | None) -> None:
        """Reflect an in-app login/logout: update the shown login, then refetch."""
        self._login = login
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#home_identity", Static).update(_identity_text(self._login))
        btn = self.query_one("#home_auth", Button)
        status = self.query_one("#home_status", Static)
        if not self._login:
            btn.label = "Log in with GitHub"
            status.display = False
            return
        btn.label = "Log out"
        status.display = True
        programs = _registered_count(HubClient(self._hub), self._login)
        totals = run_totals(read_runs(_RUNS_DIR))
        status.update(" " + platform_status_line(
            programs=programs, runs=totals["runs"], wins=totals["wins"],
            tokens_display=_compact(totals["tokens"]),
        ) + " ")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "home_auth":
            return
        app = cast("NetHackersApp", self.app)
        if self._login:
            app.action_logout()
        else:
            app.action_login()
