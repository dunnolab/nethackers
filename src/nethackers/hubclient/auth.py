"""Token-refresh adapter for hub registration auth.

``evolve``'s win-path used to hand ``HubClient.register`` the raw stored
``access_token``: once it expired the hub 401'd, ``harness.loop`` caught the
generic exception and downgraded the win to an unpersisted ``report(...)``
line, and the win silently stayed a LOCAL-ONLY elite even though a perfectly
good ``refresh_token`` was sitting unused in ``~/.nethackers/credentials.json``
(``gh``-based publishing uses separate auth, so the solution itself still
landed on GitHub -- it just never registered on the hub). ``TokenSource``
closes that gap: it wraps a ``Credentials`` and knows how to keep a usable
access token flowing -- proactively on ``.current()`` (mirrors the CLI's
one-shot ``cli._authed_token()``) and reactively on ``.refresh()`` (used by
``HubClient`` after an actual 401, e.g. clock skew or a mid-run revocation).
Both paths persist the rebuilt ``Credentials`` via ``save`` so a refreshed
token survives past this process.

``AuthError`` is what a caller (``HubClient``, then ``harness.loop``'s
win-path) turns into a ``local-only`` outcome instead of a crash or a
swallowed exception -- the whole point of this module: a hub-auth failure
must be visible (`hub_reason` / metrics / the monitor ledger), never silent.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from nethackers.hubclient import credentials as _credentials
from nethackers.hubclient.credentials import Credentials
from nethackers.hubclient.register import refresh_access_token as _refresh_access_token

_LOGIN_HINT = "hub token expired and could not refresh — run `nethackers login`"


class AuthError(Exception):
    """The hub token is unusable and there is no way to get a fresh one:
    either there is no ``refresh_token`` to exchange, or the exchange itself
    failed (expired/revoked refresh token, hub/GitHub unreachable, ...)."""


class TokenSource:
    """Keeps one ``Credentials``'s access token fresh across a run.

    ``save``/``refresh``/``now`` are injectable seams (default: the real
    ``credentials.save``, ``register.refresh_access_token``, ``time.time``)
    so tests drive proactive/reactive refresh without touching disk or the
    network.
    """

    def __init__(
        self,
        creds: Credentials,
        *,
        save: Callable[[Credentials], None] = _credentials.save,
        refresh: Callable[[str], dict[str, Any]] = _refresh_access_token,
        load: Callable[[], Credentials | None] = _credentials.load,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._creds = creds
        self._save = save
        self._refresh = refresh
        self._load = load   # re-read disk so parallel runs share a rotated token
        self._now = now

    @property
    def login(self) -> str:
        return self._creds.login

    def current(self) -> str:
        """The best token on hand right now: refreshed first (proactively)
        if the stored credential is already expired and a refresh token is
        available, so a caller almost never has to fall back to the
        reactive ``.refresh()`` on a 401. An expired credential with no
        refresh token still returns the (stale) access token as-is -- best
        effort, same as the CLI's old ``_authed_token()``; the hard failure
        surfaces later, reactively, when something actually 401s."""
        if self._creds.is_expired(self._now()) and self._creds.refresh_token:
            return self.refresh()
        return self._creds.access_token

    def _adopt_disk(self) -> str | None:
        """Re-read ``credentials.json`` (a PARALLEL evolve run sharing it may
        have just refreshed + saved a fresh token) and adopt it as our own.
        Returns a still-valid access token to REUSE outright -- i.e. a peer
        already did the refresh -- or ``None`` (we still adopted disk's latest
        ``refresh_token`` for our own exchange, since it may be newer than our
        in-memory one). Never raises -- a missing/unreadable file just leaves
        the in-memory credential in place."""
        disk = self._load()
        if disk is None:
            return None
        self._creds = disk
        if disk.access_token and not disk.is_expired(self._now()):
            return disk.access_token
        return None

    def refresh(self) -> str:
        """Force a refresh -- used reactively after the hub 401s a token that
        looked valid locally, and proactively by ``current()``.

        Cooperates with PARALLEL runs that share ``credentials.json``: GitHub
        rotates the refresh token on every use (it is single-use), so a
        sibling evolve run may have already consumed our token and saved a
        fresh one. So if our exchange fails, we re-read disk once more and
        REUSE the peer's freshly-saved token before giving up. ``AuthError``
        only when there is genuinely no usable token left anywhere -- the
        "update the token each time it fails, report only if that did not
        help" contract."""
        if self._creds.refresh_token is None:
            raise AuthError(_LOGIN_HINT)
        try:
            tok = self._refresh(self._creds.refresh_token)
        except Exception as e:
            reused = self._adopt_disk()   # a peer may have just saved a fresh token
            if reused is not None:
                return reused
            raise AuthError(_LOGIN_HINT) from e
        self._creds = Credentials(
            login=self._creds.login,
            access_token=tok["access_token"],
            refresh_token=tok["refresh_token"],
            expires_at=(self._now() + tok["expires_in"]) if tok["expires_in"] else None,
        )
        self._save(self._creds)
        return self._creds.access_token
