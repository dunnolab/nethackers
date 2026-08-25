"""In-app GitHub device-flow login: a modal that shows the authorize URL + code
(the same panel the CLI prints), runs ``device_login`` in a background thread so
the dashboard stays live, saves the resulting credential, and dismisses with it
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

from nethackers import clipboard
from nethackers.hubclient import credentials as _cred
from nethackers.hubclient.credentials import Credentials, whoami_from_token
from nethackers.hubclient.register import DeviceFlowError, device_login


class LoginModal(ModalScreen[Credentials | None]):
    """Dismisses with the new ``Credentials`` on success, or ``None`` on cancel
    / failure. ``now`` is injectable so the credential's ``expires_at`` is
    deterministic under test."""

    BINDINGS = [("escape", "cancel", "Cancel")]
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
        try:
            tok = device_login(
                prompt=lambda uri, code: self.app.call_from_thread(self._show, uri, code)
            )
            login = whoami_from_token(tok["access_token"])
        except DeviceFlowError as e:
            self.app.call_from_thread(self._fail, f"device flow: {e}")
        except Exception as e:  # network / whoami / anything terminal
            self.app.call_from_thread(self._fail, str(e))
        else:
            self.app.call_from_thread(self._done, login, tok)

    def _show(self, uri: str, code: str) -> None:
        note = "  [#00a000](copied to clipboard)[/]" if clipboard.copy(code) else ""
        # [link='…'] emits an OSC 8 hyperlink so the URL is clickable inside the
        # full-screen app (the terminal's own URL auto-detection never fires
        # here). The quotes are required -- textual's markup parser rejects a
        # bare [link=https://…] on the '://'.
        self._set_panel(
            f"[dim]1  Open in your browser[/]\n   [b #00a0a0][link='{uri}']{uri}[/link][/]\n\n"
            f"[dim]2  Enter this code[/]{note}\n   [b #ffd54a]{code}[/]\n\n"
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
        self._set_panel(f"[#c04040]login failed[/]\n[dim]{msg}[/]\n\n[dim]esc to close[/]")

    def action_cancel(self) -> None:
        self.dismiss(None)
