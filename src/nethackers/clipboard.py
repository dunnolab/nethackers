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

# xclip, xsel and wl-copy fork a small child that owns the selection and lives
# on after the copy.  Capturing its output would make ``run`` wait for that
# child to close the inherited pipes -- i.e. until the clipboard changes hands
# -- so the helpers' output goes to /dev/null instead.  The timeout is the
# backstop for a genuinely stuck tool: login calls this before it can present
# the device code, so the best-effort nicety stays strictly bounded.
_COPY_TIMEOUT = 1.0

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
            run(
                list(cmd), input=text, text=True, check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_COPY_TIMEOUT,
            )
            return True
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ):
            continue
    return False
