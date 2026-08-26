"""Tests for ``nethackers.hubclient.auth`` -- the token-refresh adapter that
keeps a ``HubClient``'s Bearer token usable across a run: ``.current()``
refreshes proactively (mirrors the CLI's one-shot ``_authed_token()``),
``.refresh()`` forces a reactive refresh (used by ``HubClient`` after a 401).
``save``/``refresh``/``now`` are injected so none of this touches disk or the
network."""
from __future__ import annotations

import pytest

from nethackers.hubclient.auth import AuthError, TokenSource
from nethackers.hubclient.credentials import Credentials


def _creds(expires_at=None, refresh_token="ghr-1", access_token="ghu-old"):
    return Credentials(login="sam", access_token=access_token,
                       refresh_token=refresh_token, expires_at=expires_at)


# --- .current() -- proactive refresh --------------------------------------


def test_current_returns_access_token_when_not_expired():
    calls = []
    source = TokenSource(_creds(expires_at=None), refresh=lambda rt: calls.append(rt) or {})
    assert source.current() == "ghu-old"
    assert calls == []  # never expired -> never refreshed


def test_current_returns_access_token_when_expiry_still_in_the_future():
    source = TokenSource(_creds(expires_at=1000.0), now=lambda: 100.0,
                         refresh=lambda rt: (_ for _ in ()).throw(AssertionError("must not run")))
    assert source.current() == "ghu-old"


def test_current_proactively_refreshes_when_expired():
    seen_rt = []

    def fake_refresh(rt):
        seen_rt.append(rt)
        return {"access_token": "ghu-new", "refresh_token": "ghr-2", "expires_in": 28800}

    saved = []
    source = TokenSource(_creds(expires_at=500.0), refresh=fake_refresh,
                         save=saved.append, now=lambda: 1000.0)
    assert source.current() == "ghu-new"
    assert seen_rt == ["ghr-1"]
    assert saved == [Credentials("sam", "ghu-new", "ghr-2", 1000.0 + 28800)]


def test_current_returns_stale_token_when_expired_with_no_refresh_token():
    # Best-effort, matching cli._authed_token()'s old behavior: with nothing
    # to refresh from, hand back the (already-expired) token as-is rather
    # than raising here -- the reactive .refresh() path (triggered by an
    # actual 401) is where a hard failure belongs.
    source = TokenSource(_creds(expires_at=500.0, refresh_token=None), now=lambda: 1000.0)
    assert source.current() == "ghu-old"


def test_current_raises_autherror_when_proactive_refresh_fails():
    def boom(rt):
        raise RuntimeError("refresh token revoked")

    source = TokenSource(_creds(expires_at=500.0), refresh=boom, load=lambda: None,
                         now=lambda: 1000.0)
    with pytest.raises(AuthError):
        source.current()


# --- .refresh() -- forced/reactive refresh --------------------------------


def test_refresh_forces_exchange_even_when_not_expired():
    calls = []

    def fake_refresh(rt):
        calls.append(rt)
        return {"access_token": "ghu-new", "refresh_token": "ghr-2", "expires_in": 28800}

    source = TokenSource(_creds(expires_at=None), refresh=fake_refresh,
                         save=lambda c: None, now=lambda: 1000.0)
    assert source.refresh() == "ghu-new"
    assert calls == ["ghr-1"]
    assert source.current() == "ghu-new"  # internal credential state advanced


def test_refresh_persists_rebuilt_credentials_with_recomputed_expiry():
    saved = []
    source = TokenSource(
        _creds(expires_at=None),
        refresh=lambda rt: {"access_token": "ghu-new", "refresh_token": "ghr-2",
                            "expires_in": 3600},
        save=saved.append, now=lambda: 100.0)
    source.refresh()
    assert saved == [Credentials("sam", "ghu-new", "ghr-2", 3700.0)]


def test_refresh_with_no_expires_in_clears_expiry():
    saved = []
    source = TokenSource(
        _creds(expires_at=1000.0),
        refresh=lambda rt: {"access_token": "ghu-new", "refresh_token": None,
                            "expires_in": None},
        save=saved.append, now=lambda: 100.0)
    assert source.refresh() == "ghu-new"
    assert saved == [Credentials("sam", "ghu-new", None, None)]


def test_refresh_raises_autherror_when_no_refresh_token():
    # No refresh/save override needed -- the None-refresh_token guard must
    # short-circuit before either the real network call or the real disk
    # write the defaults would otherwise perform.
    source = TokenSource(_creds(refresh_token=None))
    with pytest.raises(AuthError, match="run `nethackers login`"):
        source.refresh()


def test_refresh_raises_autherror_when_refresh_call_raises():
    def boom(rt):
        raise RuntimeError("bad refresh token")

    source = TokenSource(_creds(), refresh=boom, load=lambda: None)
    with pytest.raises(AuthError, match="run `nethackers login`") as exc_info:
        source.refresh()
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_refresh_reuses_a_peers_fresh_token_after_a_lost_rotation_race():
    # GitHub rotates the single-use refresh token: a PARALLEL run sharing
    # credentials.json already refreshed + saved a fresh token, so OUR exchange
    # of the now-consumed token fails -- but we must re-read disk and reuse the
    # peer's token, NOT report a failure.
    def boom(rt):
        raise RuntimeError("refresh token already used")

    peer = Credentials("sam", "ghu-peer", "ghr-peer", expires_at=1000.0)
    source = TokenSource(_creds(expires_at=500.0), refresh=boom,
                         load=lambda: peer, now=lambda: 100.0)  # peer still valid at now=100
    assert source.refresh() == "ghu-peer"   # reused a peer's token, no AuthError
    assert source.current() == "ghu-peer"   # adopted the peer credential


def test_refresh_raises_when_exchange_fails_and_disk_is_also_stale():
    # Genuinely unrecoverable: our exchange fails AND disk has no fresh token
    # either (no peer refreshed) -> report, per the "only if it did not help".
    def boom(rt):
        raise RuntimeError("refresh token revoked")

    stale = Credentials("sam", "ghu-stale", "ghr-stale", expires_at=500.0)
    source = TokenSource(_creds(expires_at=500.0), refresh=boom,
                         load=lambda: stale, now=lambda: 1000.0)  # disk also expired
    with pytest.raises(AuthError, match="run `nethackers login`"):
        source.refresh()


def test_refresh_failure_does_not_persist_anything():
    saved = []
    source = TokenSource(_creds(refresh_token=None), save=saved.append)
    with pytest.raises(AuthError):
        source.refresh()
    assert saved == []


# --- misc -------------------------------------------------------------------


def test_login_property_exposes_the_credentials_login():
    source = TokenSource(_creds())
    assert source.login == "sam"
