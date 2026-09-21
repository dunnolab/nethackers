"""Best-effort open-a-URL-in-the-user's-browser, so the login flows can say
"Press Enter to open <url> in your browser" (as ``gh auth login`` does) instead
of relying on a clickable terminal link, which only some terminals honor.

``can_open`` answers whether there is a browser to hand a URL to at all.
Outside macOS and Windows that takes a display (``DISPLAY``/
``WAYLAND_DISPLAY``) or an explicit ``$BROWSER`` -- VS Code Remote-SSH points
it at a helper that opens the URL on the laptop. Without one (plain SSH to a
server) ``webbrowser`` falls back to a console browser -- lynx, w3m, ... --
that runs in the foreground and takes the terminal over (and garbles the TUI),
so that counts as no browser too. gcloud and the Azure CLI gate their own
login flows on the same check.

Callers treat the browser as a nicety, never a requirement: the URL stays on
screen to open by hand, and neither function raises. The environment and
launcher are injectable for tests."""
from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Callable, Mapping

# Console browsers ``webbrowser`` registers when $TERM is set -- the fallbacks
# on a machine with no display.
_CONSOLE_BROWSERS = frozenset({"www-browser", "links", "elinks", "lynx", "w3m"})


def can_open(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    get: Callable[[], object] | None = None,
) -> bool:
    """Whether a URL can be opened in a (non-console) browser here."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    get = webbrowser.get if get is None else get
    needs_display = platform != "darwin" and not platform.startswith("win")
    if needs_display and not any(
        environ.get(var) for var in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER")
    ):
        return False
    try:
        found = get()
    except Exception:  # webbrowser.Error, or a broken launcher probe
        return False
    return getattr(found, "name", "") not in _CONSOLE_BROWSERS


def open_url(url: str, *, launch: Callable[[str], bool] | None = None) -> bool:
    """Open ``url`` in the default browser; return ``True`` if a browser took it."""
    launch = webbrowser.open if launch is None else launch
    try:
        return bool(launch(url))
    except Exception:  # a launcher failure must never fail the caller
        return False
