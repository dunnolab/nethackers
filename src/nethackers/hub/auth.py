"""Pluggable hub authentication (M2a Task 10): an ``AuthProvider`` resolves a
presented token to a GitHub login. ``LocalStubAuth`` maps stub tokens to
logins offline and drives all local/API tests; ``GitHubAppAuth`` validates a
device-flow *user* token against GitHub's ``GET /user`` endpoint.

The device-flow *initiation* that mints a user token is client-side (Task
13's hubclient) -- the hub only ever validates a token a client already has.
Per M2a's no-hub-secret decision, validation never sends a client secret;
``client_id`` is stored on ``GitHubAppAuth`` as config only.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class AuthError(Exception):
    """Raised when a presented token cannot be resolved to a login."""


class AuthProvider(Protocol):
    """Resolves a presented token to the caller's login."""

    #: This provider's auth mode, reported verbatim by ``GET /healthz``'s
    #: ``auth`` field (an additive field -- see api.py's module docstring)
    #: so a client pointed at a local hub can tell an offline stub hub
    #: (``"offline"``) from a real GitHub-backed one (``"github"``) instead
    #: of discovering the mismatch as a bare 401 on register.
    mode: str

    def resolve(self, token: str) -> str:
        """Return the login for ``token``, or raise ``AuthError``."""


class LocalStubAuth:
    """Offline ``AuthProvider`` backed by a fixed token->login map.

    No network. Drives local/API tests: construct with the tokens a test
    needs (e.g. ``{"tok-sam": "sam"}``) and resolve against them.
    """

    mode = "offline"

    def __init__(self, identities: dict[str, str]):
        self._identities = identities

    def resolve(self, token: str) -> str:
        try:
            return self._identities[token]
        except KeyError:
            raise AuthError(f"unknown stub token: {token!r}") from None


class GitHubAppAuth:
    """``AuthProvider`` that validates a device-flow user token against
    GitHub's REST API.

    ``client_id`` is stored as config only -- validation itself uses no
    client secret (M2a's no-hub-secret design). ``http`` defaults to the
    ``httpx`` module and is injectable so tests can supply a fake with a
    ``.get(url, headers=...)`` method instead of hitting the network.
    """

    mode = "github"

    def __init__(self, client_id: str, *, http: Any = httpx):
        self.client_id = client_id
        self._http = http

    def resolve(self, token: str) -> str:
        resp = self._http.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"github token validation failed: {resp.status_code}")
        login: str = resp.json()["login"]
        return login


def owns_repo(login: str, repo: str) -> bool:
    """Whether ``repo``'s owner segment equals ``login``.

    ``repo`` may be a bare ``"owner/name"``, a host-relative
    ``"github.com/owner/name"``, or a full ``"https://github.com/owner/name"``
    -- the owner is always the second-to-last ``/``-separated segment.
    """
    owner = repo.rstrip("/").split("/")[-2]
    return owner == login
