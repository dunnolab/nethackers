"""Shared NetHack-flavored visual language: status→glyph/style maps (reusing
the hubclient.live maps so colors never drift across screens), a tty color
palette, and the app's Textual CSS. Pure data; unit-tested.

The look is a dungeon-dark ground (near-black stone) with a single warm
**amber/lantern-gold** accent -- deliberately not the acid-green-on-black
default -- and every section framed as a titled "tty window" (heavy
box-drawing borders), echoing NetHack's own boxed inventory/menu windows.
"""
from __future__ import annotations

from nethackers.hubclient.live import _STATUS_STYLE, _progress_style

GLYPH: dict[str, str] = {
    "ascended": "★", "completed": "✓", "died": "☠",
    "aborted": "…", "error": "×", "running": "@",
}


def glyph(status: str) -> str:
    return GLYPH.get(status, "·")


def status_style(status: str) -> str:
    return _STATUS_STYLE.get(status, "")


def progress_style(progress: float) -> str:
    return _progress_style(progress)


# NetHack tty palette (name -> hex). `gold`/`hp`/`green`/... feed rich-text
# glyph styling; the dashboard chrome uses the DUNGEON/AMBER/PARCHMENT set.
PALETTE: dict[str, str] = {
    "hp": "#c04040", "gold": "#c0a000", "cyan": "#00a0a0",
    "magenta": "#a000a0", "green": "#00a000", "parchment": "#d7c9a2",
    "stone": "#16161c",
    "dungeon": "#0b0b0e", "panel": "#16161c", "amber": "#d2a24c",
    "dim": "#7c745f",
}

_DUNGEON = PALETTE["dungeon"]
_PANEL = PALETTE["panel"]
_PARCHMENT = PALETTE["parchment"]
_AMBER = PALETTE["amber"]
_DIM = PALETTE["dim"]

# App-wide stylesheet. Shared, reusable classes only; per-screen layout lives
# in each view's DEFAULT_CSS so specificity stays flat.
CSS: str = f"""
Screen {{
    background: {_DUNGEON};
    color: {_PARCHMENT};
}}

/* top identity band, then the section tab bar (active tab in amber).
   Both sit in normal flow -- two dock:top siblings overlap. */
.idbar {{
    height: 1;
    background: {_PANEL};
    color: {_DIM};
    padding: 0 1;
}}
#nav {{
    background: {_PANEL};
    color: {_DIM};
}}
#nav Tab.-active {{
    background: {_PANEL};
    color: {_AMBER};
    text-style: bold;
}}
#nav Underline > .underline--bar {{
    color: {_AMBER};
}}
#body {{ height: 1fr; }}

/* MapView's own Frontier subtab bar (Universe/Program) -- same amber-active,
   dim-inactive language as #nav, never Textual's blue default. */
#ftabs {{
    background: {_PANEL};
    color: {_DIM};
}}
#ftabs Tab.-active {{
    background: {_PANEL};
    color: {_AMBER};
    text-style: bold;
}}
#ftabs Underline > .underline--bar {{
    color: {_AMBER};
}}

/* the signature framed, titled "tty window" */
.panel {{
    background: {_PANEL};
    color: {_PARCHMENT};
    border: heavy {_AMBER};
    border-title-align: left;
    border-title-color: {_AMBER};
    border-title-style: bold;
    padding: 0 1;
}}

/* muted body text for empty/idle states */
.muted {{
    color: {_DIM};
    padding: 1 1;
}}

/* a NetHack-style status line: sits in flow just above the app Footer
   (the monitor's TabbedContent is 1fr, so this single row lands at the
   bottom of the content area). Not docked -- two bottom-docked bars
   (this + Footer) collide. */
.statusline {{
    height: 1;
    background: {_AMBER};
    color: {_DUNGEON};
    text-style: bold;
    padding: 0 1;
}}
"""
