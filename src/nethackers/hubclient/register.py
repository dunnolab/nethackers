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
# Public GitHub App client id; a placeholder until the real NetHackers App is
# registered (M2a stands up no live GitHub App -- see hub/auth.py's
# GitHubAppAuth, which resolves whatever token this flow produces).
# Overridable via NETHACKERS_CLIENT_ID so a real id can be swapped in later
# without a code change.
DEFAULT_CLIENT_ID = os.environ.get("NETHACKERS_CLIENT_ID", "Iv1.nethackers-dev")


class DeviceFlowError(Exception):
    """The device flow ended in a terminal error (e.g. ``access_denied``,
    ``expired_token``) that polling can never resolve."""


def register_solution(
    *,
    hub,
    reference: dict[str, Any],
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    client_id: str = DEFAULT_CLIENT_ID,
    http=httpx,
    prompt=print,
    sleep=time.sleep,
) -> Any:
    """Run the GitHub device flow to get a user token, then call
    ``hub.register(token=..., reference=reference, manifest=manifest,
    evidence=evidence)`` and return its result.

    Requests a device code, ``prompt``s the user with the verification URL
    and the code to enter there, then polls the token endpoint every
    ``interval`` seconds (``sleep``) while the server reports
    ``authorization_pending``/``slow_down``. Any other error
    (``access_denied``, ``expired_token``, ...) is terminal and raises
    ``DeviceFlowError`` before ``hub.register`` is ever called.
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
            return hub.register(
                token=token_response["access_token"],
                reference=reference,
                manifest=manifest,
                evidence=evidence,
            )
        if token_response.get("error") in ("authorization_pending", "slow_down"):
            sleep(interval)
            continue
        raise DeviceFlowError(token_response.get("error") or "device flow failed")
