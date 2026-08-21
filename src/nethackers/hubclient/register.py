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

import os
import time
from typing import Any

import httpx

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# Public GitHub App client id -- a public value, not a secret. The literal is
# a placeholder replaced at App registration (M2a stands up no live GitHub App
# -- see hub/auth.py's GitHubAppAuth, which resolves whatever token this flow
# produces). Overridable via NETHACKERS_CLIENT_ID so a real id can be swapped
# in later without a code change.
NETHACKERS_APP_CLIENT_ID = "Iv1.PLACEHOLDER_REPLACE_AT_APP_REGISTRATION"
DEFAULT_CLIENT_ID = os.environ.get("NETHACKERS_CLIENT_ID", NETHACKERS_APP_CLIENT_ID)


class DeviceFlowError(Exception):
    """The device flow ended in a terminal error (e.g. ``access_denied``,
    ``expired_token``) that polling can never resolve."""


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


def device_login(
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    http=httpx,
    prompt=print,
    sleep=time.sleep,
) -> dict[str, Any]:
    """Run the GitHub device flow and return the resulting user token set.

    Requests a device code, ``prompt``s the user with the verification URL
    and the code to enter there, then polls the token endpoint every
    ``interval`` seconds (``sleep``) while the server reports
    ``authorization_pending``/``slow_down``. Any other error
    (``access_denied``, ``expired_token``, ...) is terminal and raises
    ``DeviceFlowError``.

    Returns the ``_token_set`` dict -- ``{"access_token", "refresh_token" |
    None, "expires_in" | None}`` -- so callers can persist a refreshable
    credential rather than just a bare access token.
    """

    def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = http.post(url, data=payload, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    device = _post(GITHUB_DEVICE_CODE_URL, {"client_id": client_id, "scope": ""})
    prompt(
        f"To authorize, open {device['verification_uri']} and enter code: {device['user_code']}"
    )
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
    client_id: str = DEFAULT_CLIENT_ID,
    http=httpx,
) -> dict[str, Any]:
    """Exchange a GitHub refresh token for a fresh user token set.

    POSTs ``grant_type=refresh_token`` to the token endpoint and returns the
    same ``_token_set`` shape as ``device_login``. A response without an
    ``access_token`` (e.g. ``bad_refresh_token``) is terminal and raises
    ``DeviceFlowError`` -- the caller must fall back to a full ``device_login``.
    """
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
    token_response = response.json()
    if "access_token" not in token_response:
        raise DeviceFlowError(token_response.get("error") or "refresh failed")
    return _token_set(token_response)
