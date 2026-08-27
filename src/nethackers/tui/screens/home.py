"""Textual Home view: the NetHackers landing screen. An ASCII "NETHACKERS"
wordmark over a one-line description, your ``@``-hero identity with a clear
login/logout control, and -- once you're logged in -- your standing rendered as
a NetHack status line (Programs / Runs / Wins / Evolved-tokens).

Score-based boards (leaderboard, frontier, elites) live in their own tabs; Home
is the identity-and-standing hero, so it stays legible even before any scores
exist. ``recent_runs_panel`` stays here because the Runs tab reuses it.
"""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from rich import box
from rich.align import Align
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Button, Static
from textual.worker import get_current_worker

from nethackers.config import load_stage
from nethackers.hubclient.client import HubClient
from nethackers.tui.art import NETHACKERS_BANNER, platform_status_line
from nethackers.tui.screens.runs import read_runs, run_causes, run_totals
from nethackers.tui.status import _compact

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

# Give up on the standing's program count fast so the worker never lingers;
# Home stays responsive and just shows "—" if the hub is slow (see hub._HUB_TIMEOUT).
_HUB_TIMEOUT = 4.0

_TAGLINE = ("Evolve symbolic NetHack players with coding agents.\n"
            "Register your best — climb the shared hub.")

# The RIP signature tombstone -- the website footer's (hub/web/index.html),
# ported verbatim. Backslashes are escaped; kept as a line list so the shape is
# obvious and never mangled by string-literal continuation rules.
_TOMBSTONE = "\n".join([
    "       _____________",
    "      /             \\",
    "     /     REST      \\",
    "    /       IN        \\",
    "   /      PEACE        \\",
    "  /                     \\",
    "  |     no program      |",
    "  |    has ascended     |",
    "  |    NetHack 3.6.6    |",
    "  |        yet.         |",
    "  |                     |",
    "  |   will yours be     |",
    "  |     the first?      |",
    "* |   *    *    *       | *",
    "__)/\\_//(\\/(/\\)/\\//\\/\\|_)__",
])


def tombstone() -> Align:
    """The RIP tombstone as a centred block; the version epitaph in red, like
    the website's ``.epi``. Centred as a whole (Align, not per-line text-align)
    so the art's internal alignment is preserved."""
    art = Text(_TOMBSTONE, style="#6f6a5f", no_wrap=True)
    art.highlight_words(["NetHack 3.6.6"], style="bold #c9403a")
    return Align.center(art)


def recent_runs_panel(runs: list[dict[str, Any]]) -> Table:
    """The most recent local evolve runs (``read_runs``'s per-run summaries):
    run id, objective, operator, a ``wins/iterations`` fraction, and the best
    dev score. Capped to the 6 most recent (already newest-first). Kept here
    because the Runs tab reuses it."""
    t = Table(header_style="bold", pad_edge=False, box=box.SIMPLE_HEAVY)
    for c in ("run", "objective", "op", "wins", "dev"):
        t.add_column(c)
    for r in runs[:6]:
        t.add_row(
            str(r["run_id"]), str(r["objective"]), str(r.get("operator", "")),
            f"{r['wins']}/{r['iterations']}",
            f"{(r.get('best_dev') or 0):.2f}",
        )
    return t


def causes_panel(causes: dict[str, int]) -> Table:
    """The top ways this user's policies have died across their local runs:
    a verbatim NetHack cause and how many times it happened, most-frequent
    first, capped to 8 rows. The dungeon meme, quantified."""
    t = Table(header_style="bold", pad_edge=False, box=box.SIMPLE_HEAVY,
              title="☠ causes of death", title_style="bold #d2a24c")
    t.add_column("cause")
    t.add_column("×", justify="right")
    for cause, n in sorted(causes.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        t.add_row(cause, str(n))
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


class HomeView(VerticalScroll):
    """The landing hero: wordmark + description + identity + login/logout, and
    your NetHack-style standing line once you're logged in. Refetched on mount
    and every time Home is shown again.

    A ``VerticalScroll`` (not a plain ``Vertical``) so a short terminal can
    scroll to the whole card -- the tombstone and the log-in/out button below it
    -- instead of clipping them off the bottom with no way to reach them; the
    card still centres when it does fit."""

    # Scrollable, but not a keyboard-focus/arrow-nav target itself (VerticalScroll
    # is focusable by default): the modal navigator dives from the Home tab
    # straight to the card's one control (the button), which scroll_visible()
    # scrolls into view -- so a focusable pane here would just intercept ↑/↓.
    # The mouse wheel and scrollbar still scroll it regardless.
    can_focus = False

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
    HomeView #home_causes { width: 100%; margin-top: 1; }
    HomeView #home_tomb { width: 100%; margin-top: 1; }
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
            yield Static(id="home_causes")
            yield Static(tombstone(), id="home_tomb")
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
        causes_widget = self.query_one("#home_causes", Static)
        if not self._login:
            btn.label = "Log in with GitHub"
            status.display = False
            causes_widget.display = False
            return
        btn.label = "Log out"
        status.display = True
        runs = read_runs(load_stage().runs_dir)  # local files, cheap
        totals = run_totals(runs)
        # Paint the standing immediately with programs unknown ("—"); the count
        # is the one hub round-trip here, so fetch it on a worker and fill it in
        # when it lands rather than blocking the landing screen on it.
        self._render_status(status, None, totals)
        causes = run_causes(runs)
        if causes:
            causes_widget.display = True
            causes_widget.update(Align.center(causes_panel(causes)))
        else:
            causes_widget.display = False
        self._fetch_programs(self._login, totals)

    def _render_status(self, status: Static, programs: int | None,
                       totals: dict[str, Any]) -> None:
        status.update(" " + platform_status_line(
            programs=programs, runs=totals["runs"], wins=totals["wins"],
            tokens_display=_compact(totals["tokens"]),
        ) + " ")

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_programs(self, login: str, totals: dict[str, Any]) -> None:
        # _registered_count already degrades a hub error to None (-> "—").
        programs = _registered_count(HubClient(self._hub, timeout=_HUB_TIMEOUT), login)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self._apply_programs, programs, totals)

    def _apply_programs(self, programs: int | None, totals: dict[str, Any]) -> None:
        # Only if we're still logged in as the same user with the line shown.
        if not self._login:
            return
        with contextlib.suppress(Exception):
            status = self.query_one("#home_status", Static)
            if status.display:
                self._render_status(status, programs, totals)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "home_auth":
            return
        app = cast("NetHackersApp", self.app)
        if self._login:
            app.action_logout()
        else:
            app.action_login()
