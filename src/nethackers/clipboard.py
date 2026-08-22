"""Best-effort copy-to-system-clipboard, so the login flows can put the device
code on the clipboard for the user to paste (in the TUI, Textual captures the
mouse, so selecting the code by hand doesn't work at all).

Tries the platform tools in turn -- macOS ``pbcopy``, Wayland ``wl-copy``, X11
``xclip``/``xsel``, Windows ``clip`` -- and returns whether any succeeded.
Callers treat the clipboard as a nicety, never a requirement: on failure they
just leave the code on screen to type. ``run`` is injectable for tests."""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

Run = Callable[..., object]

_CANDIDATES: tuple[Sequence[str], ...] = (
    ("pbcopy",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
    ("clip",),
)


def copy(text: str, *, run: Run = subprocess.run) -> bool:
    """Copy ``text`` to the system clipboard; return ``True`` if a tool worked."""
    for cmd in _CANDIDATES:
        try:
            run(list(cmd), input=text, text=True, check=True, capture_output=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            continue
    return False
