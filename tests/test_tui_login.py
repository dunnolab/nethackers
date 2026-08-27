"""Tests for the in-app GitHub device-flow login (``LoginModal``) and its
wiring into ``NetHackersApp``.

The device flow itself (``device_login``/``whoami_from_token``) and the
credential store's ``path`` are monkeypatched, so no real GitHub network call
or write to ``~/.nethackers/credentials.json`` ever happens. The modal's
``@work(thread=True)`` flow is driven for real where it's cheap (an instant,
monkeypatched ``device_login``), and short-circuited (``_flow`` neutralized to
a no-op, or ``action_cancel``/``_show`` called directly) where driving the live
thread would be racy -- exactly the escape hatch the modal exposes for tests.
"""
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Static

from nethackers.hubclient import credentials
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens import login as login_mod
from nethackers.tui.screens.login import LoginModal

# nothing listens on port 1 -> the hub-backed refreshes fail near-instantly
_DEAD_HUB = "http://127.0.0.1:1"
_TOKEN = {"access_token": "ghu_x", "refresh_token": "ghr_y", "expires_in": 28800}


class _Host(App):
    """A minimal App to push the modal into -- the modal's worker needs a live
    ``self.app`` (``call_from_thread``), nothing more."""

    def compose(self) -> ComposeResult:
        yield Static()


# --- LoginModal: the happy path saves a credential and dismisses with it ----


async def test_login_modal_saves_credential_and_dismisses(monkeypatch, tmp_path):
    monkeypatch.setattr(login_mod, "device_login", lambda **kw: dict(_TOKEN))
    monkeypatch.setattr(login_mod, "whoami_from_token", lambda tok: "sam")
    monkeypatch.setattr(credentials, "path", lambda: tmp_path / "credentials.json")

    result = {}
    host = _Host()
    async with host.run_test():
        # inject a fixed clock so expires_at is deterministic
        host.push_screen(LoginModal(now=lambda: 1000.0),
                         lambda c: result.update(creds=c))
        for _ in range(200):  # let the background flow finish (up to ~2s)
            await asyncio.sleep(0.01)
            if "creds" in result:
                break

    creds = result.get("creds")
    assert isinstance(creds, Credentials)
    assert creds.login == "sam"
    assert creds.access_token == "ghu_x"
    assert creds.refresh_token == "ghr_y"
    assert creds.expires_at == 1000.0 + 28800  # now() + expires_in
    # ...and it was actually persisted through credentials.save(), not just handed back
    assert (tmp_path / "credentials.json").exists()
    assert credentials.load() == creds


# --- LoginModal: cancel dismisses with None (no credential) -----------------


async def test_login_modal_cancel_dismisses_none(monkeypatch):
    # neutralize the auto-started device flow so nothing races us to a dismiss
    monkeypatch.setattr(login_mod.LoginModal, "_flow", lambda self: None)

    result = {}
    host = _Host()
    async with host.run_test() as pilot:
        modal = LoginModal()
        host.push_screen(modal, lambda c: result.update(creds=c))
        await pilot.pause()
        modal.action_cancel()  # esc's binding
        await pilot.pause()

    assert "creds" in result and result["creds"] is None


# --- LoginModal._show: renders the URL + code and copies the code -----------


async def test_login_modal_show_displays_code_and_copies_it(monkeypatch):
    monkeypatch.setattr(login_mod.LoginModal, "_flow", lambda self: None)  # no auto-flow
    copied = {}

    def fake_copy(text):
        copied["code"] = text
        return True

    monkeypatch.setattr(login_mod.clipboard, "copy", fake_copy)

    host = _Host()
    async with host.run_test() as pilot:
        modal = LoginModal()
        host.push_screen(modal)
        await pilot.pause()
        modal._show("https://github.com/login/device", "WDJB-MJHT")
        await pilot.pause()
        panel = str(modal.query_one("#login_panel", Static).render())

    assert "WDJB-MJHT" in panel                     # the code the user types
    assert "github.com/login/device" in panel       # the URL to open
    assert copied["code"] == "WDJB-MJHT"             # the code was put on the clipboard


async def test_login_modal_url_is_clickable_hyperlink(monkeypatch):
    """The URL in the modal is an OSC 8 terminal hyperlink, not plain styled
    text -- so it is clickable inside the full-screen app, where the terminal's
    own URL auto-detection never fires."""
    import io

    from rich.console import Console

    monkeypatch.setattr(login_mod.LoginModal, "_flow", lambda self: None)  # no auto-flow
    monkeypatch.setattr(login_mod.clipboard, "copy", lambda _s: False)

    url = "https://github.com/login/device"
    host = _Host()
    async with host.run_test() as pilot:
        modal = LoginModal()
        host.push_screen(modal)
        await pilot.pause()
        modal._show(url, "WDJB-MJHT")
        await pilot.pause()
        renderable = modal.query_one("#login_panel", Static).render()

    buf = io.StringIO()
    Console(file=buf, force_terminal=True, width=100).print(renderable)
    out = buf.getvalue()

    assert "\x1b]8;" in out              # an OSC 8 hyperlink is emitted at all
    assert f";{url}\x1b\\" in out        # ...and its target is the verification URL


# --- app wiring: login adopts the credential + repaints the idbar; logout clears


async def test_app_login_wires_creds_and_idbar_then_logout_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(login_mod, "device_login", lambda **kw: dict(_TOKEN))
    monkeypatch.setattr(login_mod, "whoami_from_token", lambda tok: "sam")
    monkeypatch.setattr(credentials, "path", lambda: tmp_path / "credentials.json")
    # keep Home's standing refresh hermetic (never touch the real runs dir)
    monkeypatch.setenv("NETHACKERS_DATA_ROOT", str(tmp_path))

    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._creds is None
        assert "guest" in str(app.query_one(".idbar").render())

        app.action_login()  # pushes LoginModal; its worker logs in + hands creds back
        for _ in range(200):
            await asyncio.sleep(0.01)
            if app._creds is not None:
                break
        await pilot.pause()

        assert app._creds is not None and app._creds.login == "sam"
        assert "@sam" in str(app.query_one(".idbar").render())  # idbar repainted

        app.action_logout()
        await pilot.pause()
        assert app._creds is None
        idbar = str(app.query_one(".idbar").render())
        assert "guest" in idbar and "@sam" not in idbar
