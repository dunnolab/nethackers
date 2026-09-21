"""In-app GitHub device-flow login: a modal that shows the one-time code (the
same flow the CLI prints: copied to the clipboard, Enter opens the authorize
URL in the browser), runs ``device_login`` in a background thread so the
dashboard stays live, saves the resulting credential, and dismisses with it
-- or ``None`` on cancel/failure. Reuses ``hubclient``'s device flow verbatim,
so there is no second copy of the auth logic."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

from nethackers import browser, clipboard
from nethackers.hubclient import credentials as _cred
from nethackers.hubclient.credentials import Credentials, whoami_from_token
from nethackers.hubclient.register import DeviceFlowError, device_login


class LoginModal(ModalScreen[Credentials | None]):
    """Dismisses with the new ``Credentials`` on success, or ``None`` on cancel
    / failure. ``now`` is injectable so the credential's ``expires_at`` is
    deterministic under test."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "open_browser", "Open browser")]
    DEFAULT_CSS = """
    LoginModal { align: center middle; }
    LoginModal #login_panel {
        width: auto; max-width: 62; height: auto;
        border: heavy #d2a24c; background: #16161c; color: #d7c9a2; padding: 1 3;
        border-title-color: #d2a24c; border-title-style: bold; border-title-align: left;
    }
    """

    def __init__(self, *, now: Callable[[], float] = time.time, **kw: Any) -> None:
        super().__init__(**kw)
        self._now = now
        # The code panel as state, so the clipboard and browser workers can each
        # repaint their own part without undoing the other's.
        self._code = ""
        self._copied = False
        self._open_line = ""
        self._enter_opens: str | None = None  # the URL Enter opens, once there is one

    def compose(self) -> ComposeResult:
        yield Static(id="login_panel")

    def on_mount(self) -> None:
        self.query_one("#login_panel", Static).border_title = "Authorize NetHackers"
        self._set_panel("[dim]requesting a device code…[/]")
        self._flow()

    def _set_panel(self, body: str) -> None:
        self.query_one("#login_panel", Static).update(body)

    @work(thread=True, exit_on_error=False)
    def _flow(self) -> None:
        # Asked here, off the UI thread: webbrowser's first lookup can shell out.
        can_open = browser.can_open()
        try:
            tok = device_login(
                prompt=lambda uri, code: self.app.call_from_thread(self._show, uri, code, can_open)
            )
            login = whoami_from_token(tok["access_token"])
        except DeviceFlowError as e:
            self.app.call_from_thread(self._fail, f"device flow: {e}")
        except Exception as e:  # network / whoami / anything terminal
            self.app.call_from_thread(self._fail, str(e))
        else:
            self.app.call_from_thread(self._done, login, tok)

    def _show(self, uri: str, code: str, can_open: bool) -> None:
        # Render the actionable part first.  Clipboard integration is only a
        # convenience and must never hold the device code hostage when a
        # Wayland/X11 helper is present but its display server is unavailable.
        self._code = code
        if can_open:
            # "Press Enter", the gh way, rather than a clickable (OSC 8) link:
            # only some terminals honor one, fewer still while the app holds the mouse.
            self._enter_opens = uri
            self._open_line = f"Press [b]Enter[/] to open [b #00a0a0]{uri}[/]\nin your browser"
        else:
            self._open_line = f"Open [b #00a0a0]{uri}[/] in a browser\nand enter the code there"
        self._redraw()
        self._copy_code(code)

    @work(thread=True, exit_on_error=False)
    def _copy_code(self, code: str) -> None:
        if clipboard.copy(code):
            self.app.call_from_thread(self._on_copied)

    def _on_copied(self) -> None:
        self._copied = True
        self._redraw()

    def action_open_browser(self) -> None:
        if self._enter_opens is not None:
            self._open_browser(self._enter_opens)

    @work(thread=True, exit_on_error=False)
    def _open_browser(self, uri: str) -> None:
        # A worker: a launcher can block (a $BROWSER command runs to completion).
        if not browser.open_url(uri):
            self.app.call_from_thread(self._open_failed, uri)

    def _open_failed(self, uri: str) -> None:
        self._open_line = f"[#c04040]Couldn't open a browser[/] — go to\n[b #00a0a0]{uri}[/]"
        self._redraw()

    def _redraw(self) -> None:
        note = "  [#00a000](copied to clipboard)[/]" if self._copied else ""
        self._set_panel(
            f"[dim]Your one-time code[/]{note}\n   [b #ffd54a]{self._code}[/]\n\n"
            f"{self._open_line}\n\n"
            "[dim]waiting for you to authorize…  (esc to cancel)[/]"
        )

    def _done(self, login: str, tok: dict) -> None:
        creds = Credentials(
            login=login, access_token=tok["access_token"], refresh_token=tok["refresh_token"],
            expires_at=(self._now() + tok["expires_in"]) if tok["expires_in"] else None,
        )
        _cred.save(creds)
        self.dismiss(creds)

    def _fail(self, msg: str) -> None:
        self._enter_opens = None  # that device code is dead
        self._set_panel(f"[#c04040]login failed[/]\n[dim]{msg}[/]\n\n[dim]esc to close[/]")

    def action_cancel(self) -> None:
        self.dismiss(None)
