"""Tests for ``nethackers.hub.auth`` (M2a Task 10): the pluggable
``AuthProvider`` -- ``LocalStubAuth`` (offline token->login map, drives all
local/API tests) and ``GitHubAppAuth`` (validates a device-flow user token
against GitHub's ``GET /user`` endpoint). ``GitHubAppAuth`` is exercised
against a tiny fake ``http`` stub with a ``.get(url, headers=...)`` method --
no real network calls are made.
"""

from __future__ import annotations

import pytest

from nethackers.hub.auth import AuthError, GitHubAppAuth, LocalStubAuth, owns_repo


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, str]):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, str]:
        return self._payload


class _FakeHttp:
    """Records every ``.get`` call so tests can assert on the request."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        self.calls.append((url, headers))
        return self._response


def test_local_stub_auth_resolves_known_token_to_login():
    # Property 1a: a known stub token resolves to its mapped login.
    auth = LocalStubAuth({"tok-sam": "sam"})

    assert auth.resolve("tok-sam") == "sam"


def test_local_stub_auth_raises_on_unknown_token():
    # Property 1b: an unmapped token raises AuthError -- no network involved.
    auth = LocalStubAuth({"tok-sam": "sam"})

    with pytest.raises(AuthError):
        auth.resolve("nope")


def test_github_app_auth_resolve_returns_login_from_fake_user_endpoint():
    # Property 2: a fake http client returning 200 {"login": "sam"} resolves
    # to "sam", and the request carries the token as a bearer Authorization
    # header (no client secret is sent -- M2a's no-hub-secret design).
    fake = _FakeHttp(_FakeResponse(200, {"login": "sam"}))
    auth = GitHubAppAuth("cid", http=fake)

    login = auth.resolve("token-abc")

    assert login == "sam"
    assert len(fake.calls) == 1
    url, headers = fake.calls[0]
    assert url == "https://api.github.com/user"
    assert headers["Authorization"] == "Bearer token-abc"


def test_github_app_auth_resolve_raises_auth_error_on_non_200():
    # Property 3: a fake http client returning 401 raises AuthError.
    fake = _FakeHttp(_FakeResponse(401, {}))
    auth = GitHubAppAuth("cid", http=fake)

    with pytest.raises(AuthError):
        auth.resolve("token-abc")


def test_owns_repo_true_when_login_matches_owner_segment():
    # Property 4a.
    assert owns_repo("sam", "github.com/sam/nethacker") is True


def test_owns_repo_false_when_login_does_not_match_owner_segment():
    # Property 4b.
    assert owns_repo("sam", "github.com/other/nethacker") is False


def test_owns_repo_handles_bare_owner_slash_repo():
    # owns_repo must also accept a bare "owner/name" form, not just full
    # github.com URLs.
    assert owns_repo("sam", "sam/nethacker") is True


def test_owns_repo_handles_scheme_prefixed_url():
    assert owns_repo("sam", "https://github.com/sam/nethacker") is True
