"""Client-side GitHub OAuth Device Flow (M2a Task 13): exchanges a GitHub
App client id for a short-lived device code, tells the user where to
authorize and what code to enter, polls for the resulting user access
token, and calls ``HubClient.register`` with it. The hub never sees
anything but that final user token -- ``nethackers.hub.auth``'s
``GitHubAppAuth`` (Task 10) resolves it hub-side; this module is only the
CLI-side half of the handshake, and never talks to the hub directly except
through the ``hub`` object it's given.

``http``/``prompt``/``sleep`` are all injectable (defaulting to the real
``httpx`` module, ``print``, and ``time.sleep``), so
``tests/test_hubclient.py`` drives the whole flow offline and instantly: a
fake ``http.post`` scripts the device-code response and a poll sequence,
``prompt`` just records what the user would see, and ``sleep`` is asserted
called rather than actually pausing.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from nethackers.config import load_stage
from nethackers.hubclient._deadline import DEADLINE, call_with_deadline

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# The GitHub App client id is public config, not a secret -- it lives on
# Stage.github_client_id (this is the "NetHackers Hub" App: a personal dev App
# today, swapped for the dunnolab-org App at launch). ``client_id=None`` below
# resolves it from ``load_stage()`` at call time -- never at import -- so it
# stays live to both NETHACKERS_CLIENT_ID and NETHACKERS_STAGE.


class DeviceFlowError(Exception):
    """The device flow ended in a terminal error (e.g. ``access_denied``,
    ``expired_token``) that polling can never resolve."""


class GitHubUnreachable(Exception):
    """GitHub itself couldn't be reached to run the OAuth flow -- a transport
    failure (connect/timeout/DNS) or a hung call the deadline gave up on. This
    step talks to ``github.com``, NOT the hub, so callers must never fold it
    into the generic "cannot reach the hub" message (issue #50); the CLI's
    top-level guard renders a GitHub/DNS-specific line instead. Carries the URL
    it was trying to reach as its message."""


def _token_set(resp: dict[str, Any]) -> dict[str, Any]:
    """Normalise a GitHub token response into the three keys callers rely on:
    ``access_token`` (str), ``refresh_token`` (str or ``None``), and
    ``expires_in`` (int seconds or ``None``). Absent/empty optional fields
    collapse to ``None`` so consumers never guess at their presence."""
    return {
        "access_token": str(resp["access_token"]),
        "refresh_token": (str(resp["refresh_token"]) if resp.get("refresh_token") else None),
        "expires_in": (int(resp["expires_in"]) if resp.get("expires_in") is not None else None),
    }


def _announce(verification_uri: str, user_code: str) -> None:
    """Default device-flow prompt -- two plain lines, no rich dependency at the
    library layer. The CLI passes a styled panel instead (``cli._login_prompt``)."""
    print(f"To authorize, open {verification_uri}\nand enter code: {user_code}")


def device_login(
    *,
    client_id: str | None = None,
    http=httpx,
    prompt=_announce,
    sleep=time.sleep,
    deadline: float = DEADLINE,
) -> dict[str, Any]:
    """Run the GitHub device flow and return the resulting user token set.

    Requests a device code, ``prompt``s the user with the verification URL
    and the code to enter there, then polls the token endpoint every
    ``interval`` seconds (``sleep``) while the server reports
    ``authorization_pending``/``slow_down``. Any other error
    (``access_denied``, ``expired_token``, ...) is terminal and raises
    ``DeviceFlowError``.

    ``client_id=None`` (the default) resolves to ``load_stage().
    github_client_id`` at call time -- pass one explicitly (e.g. the CLI's
    login handler threading through its own resolved ``Stage``) to avoid a
    second ``load_stage()`` call.

    Returns the ``_token_set`` dict -- ``{"access_token", "refresh_token" |
    None, "expires_in" | None}`` -- so callers can persist a refreshable
    credential rather than just a bare access token.
    """
    if client_id is None:
        client_id = load_stage().github_client_id

    def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        # httpx's own 5s default bounds connect/read; call_with_deadline adds a
        # hard wall-clock ceiling that ALSO bounds a hung getaddrinfo (which no
        # socket timeout covers -- issue #50). A transport failure or that
        # ceiling becomes GitHubUnreachable, never a raw httpx error / a hang.
        def _do() -> dict[str, Any]:
            response = http.post(url, data=payload, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()

        try:
            return call_with_deadline(_do, deadline)
        except (httpx.RequestError, TimeoutError) as exc:
            raise GitHubUnreachable(url) from exc

    device = _post(GITHUB_DEVICE_CODE_URL, {"client_id": client_id, "scope": ""})
    prompt(device["verification_uri"], device["user_code"])
    interval = int(device.get("interval", 5))

    while True:
        token_response = _post(
            GITHUB_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if "access_token" in token_response:
            return _token_set(token_response)
        if token_response.get("error") in ("authorization_pending", "slow_down"):
            sleep(interval)
            continue
        raise DeviceFlowError(token_response.get("error") or "device flow failed")


def refresh_access_token(
    refresh_token,
    *,
    client_id: str | None = None,
    http=httpx,
    deadline: float = DEADLINE,
) -> dict[str, Any]:
    """Exchange a GitHub refresh token for a fresh user token set.

    POSTs ``grant_type=refresh_token`` to the token endpoint and returns the
    same ``_token_set`` shape as ``device_login``. A response without an
    ``access_token`` (e.g. ``bad_refresh_token``) is terminal and raises
    ``DeviceFlowError`` -- the caller must fall back to a full ``device_login``.

    ``client_id=None`` (the default) resolves to ``load_stage().
    github_client_id`` at call time, same as ``device_login`` -- this is the
    path ``TokenSource``'s ``.refresh()`` actually exercises (it calls this
    with just a bare refresh token, no ``client_id``).
    """
    if client_id is None:
        client_id = load_stage().github_client_id

    def _do() -> dict[str, Any]:
        response = http.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    # Same fail-fast contract as device_login: a transport failure / hung call
    # is GitHubUnreachable (this hits github.com, not the hub -- issue #50), not
    # a raw httpx error. TokenSource.refresh catches it like any exception.
    try:
        token_response = call_with_deadline(_do, deadline)
    except (httpx.RequestError, TimeoutError) as exc:
        raise GitHubUnreachable(GITHUB_TOKEN_URL) from exc
    if "access_token" not in token_response:
        raise DeviceFlowError(token_response.get("error") or "refresh failed")
    return _token_set(token_response)
