"""Shared NetHack-flavored visual language: status→glyph/style maps (reusing
the hubclient.live maps so colors never drift across screens), a tty color
palette, and the app's Textual CSS. Pure data; unit-tested."""
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


# NetHack tty palette (name -> hex).
PALETTE: dict[str, str] = {
    "hp": "#c04040", "gold": "#c0a000", "cyan": "#00a0a0",
    "magenta": "#a000a0", "green": "#00a000", "parchment": "#d8c8a0",
    "stone": "#1c1c1c",
}

CSS: str = """
#status { dock: top; height: 3; padding: 0 1; background: $panel; color: $text; }
.tabbar { dock: top; height: 1; background: $panel-darken-1; color: $text; }
.you { text-style: bold reverse; }
"""
