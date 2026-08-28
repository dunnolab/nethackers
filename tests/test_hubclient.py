"""Tests for ``nethackers.hubclient.client.HubClient`` and
``nethackers.hubclient.register.register_solution`` -- both exercised
against fake ``http`` doubles that record calls and return canned
responses, never real ``httpx``/a real network call. ``HubClient``'s
methods are checked against Task 12's actual API surface
(``nethackers.hub.api``): same paths, same ``?param=`` names, the register
body's link shape, and the register ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import httpx
import pytest

from nethackers.hubclient import register as r
from nethackers.hubclient.auth import AuthError
from nethackers.hubclient.client import HubClient
from nethackers.hubclient.register import device_login


class _FakeResponse:
    """Stands in for ``httpx.Response``: ``raise_for_status`` is a no-op
    (these tests never exercise the error path) and ``json`` returns
    whatever payload the test scripted."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHttp:
    """A fake ``httpx`` module for ``HubClient``: records every ``get``/
    ``post`` call (method, url, params-or-body, headers) in ``self.calls``
    and always answers with ``response``."""

    def __init__(self, response=None):
        self.calls: list[tuple[object, ...]] = []
        self._response = response

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return _FakeResponse(self._response)

    def post(self, url, *, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return _FakeResponse(self._response)


# --- Property 1: each HubClient method builds the right request -----------


def test_objectives_gets_objectives_with_no_params():
    http = _FakeHttp(response=[{"name": "random"}])
    client = HubClient("http://localhost:8000", http=http)

    result = client.objectives()

    assert result == [{"name": "random"}]
    assert http.calls == [("GET", "http://localhost:8000/objectives", None)]


def test_objective_batch_gets_named_path():
    http = _FakeHttp(response={"name": "random", "batch": []})
    client = HubClient("http://localhost:8000", http=http)

    client.objective_batch("random")

    assert http.calls == [("GET", "http://localhost:8000/objectives/random/batch", None)]


def test_attainment_without_identity_sends_no_params():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.attainment()

    assert http.calls == [("GET", "http://localhost:8000/attainment", None)]


def test_attainment_with_identity_sends_identity_param():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.attainment("val-dwa-law-fem")

    assert http.calls == [
        ("GET", "http://localhost:8000/attainment", {"identity": "val-dwa-law-fem"})
    ]


def test_baseline_gets_baseline_with_no_params():
    payload = {"owner": "autoascend", "per_identity": {}, "overall": None}
    http = _FakeHttp(response=payload)
    client = HubClient("http://localhost:8000", http=http)

    assert client.baseline() == payload
    assert http.calls == [("GET", "http://localhost:8000/baseline", None)]


def test_elites_sends_objective_param():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.elites("random")

    assert http.calls == [("GET", "http://localhost:8000/elites", {"objective": "random"})]


def test_board_with_objective_sends_objective_param():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.board(objective="random")

    assert http.calls == [("GET", "http://localhost:8000/board", {"objective": "random"})]


def test_board_with_metric_sends_metric_param():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.board(metric="coverage")

    assert http.calls == [("GET", "http://localhost:8000/board", {"metric": "coverage"})]


def test_search_default_params():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.search()

    assert http.calls == [("GET", "http://localhost:8000/search", {"limit": 50, "offset": 0})]


def test_search_with_owner_and_paging():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000", http=http)

    client.search(owner="sam", limit=10, offset=5)

    assert http.calls == [
        ("GET", "http://localhost:8000/search", {"owner": "sam", "limit": 10, "offset": 5})
    ]


def test_show_gets_solution_path():
    http = _FakeHttp(response={"digest": "sha256:x"})
    client = HubClient("http://localhost:8000", http=http)

    result = client.show("sha256:x")

    assert result == {"digest": "sha256:x"}
    assert http.calls == [("GET", "http://localhost:8000/solutions/sha256:x", None)]


def test_solution_frontier_gets_frontier_path():
    http = _FakeHttp(response=[{"solution_digest": "sha256:x"}])
    client = HubClient("http://localhost:8000", http=http)

    result = client.solution_frontier("abc")

    assert result == [{"solution_digest": "sha256:x"}]
    assert http.calls == [("GET", "http://localhost:8000/solutions/abc/frontier", None)]


def test_client_register_body():
    sent = {}

    class H:
        def post(self, url, json=None, headers=None):
            sent.update(url=url, json=json, headers=headers)

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"solution_id": "x"}

            return R()

    HubClient("https://hub", http=H()).register(
        token="t",
        reference={"repo": "github.com/sam/nethacker", "commit": "c" * 40},
        manifest={"root": "bot", "entrypoint": "bot.py"},
        evidence={"solution_digest": "sha256:x"},
    )
    assert sent["json"] == {
        "reference": {"repo": "github.com/sam/nethacker", "commit": "c" * 40},
        "manifest": {"root": "bot", "entrypoint": "bot.py"},
        "evidence": {"solution_digest": "sha256:x"},
    }
    assert sent["headers"]["Authorization"] == "Bearer t"


# --- register() self-healing via an injected TokenSource -------------------
#
# No real network call in any of these -- ``httpx.Request``/``Response`` are
# just used as data so ``raise_for_status()`` raises a genuine
# ``httpx.HTTPStatusError`` (the exact type/shape ``HubClient`` branches on),
# rather than a hand-rolled stand-in exception.


class _FakeTokenSource:
    """Duck-types ``TokenSource``: ``current()`` answers whatever token is
    "on hand" right now; ``refresh()`` swaps it for ``after_refresh`` (or
    raises, when the reactive exchange itself fails) and counts its calls."""

    def __init__(self, *, current, after_refresh=None, raise_on_refresh=None):
        self._current = current
        self._after_refresh = after_refresh
        self._raise_on_refresh = raise_on_refresh
        self.refresh_calls = 0

    def current(self):
        return self._current

    def refresh(self):
        self.refresh_calls += 1
        if self._raise_on_refresh is not None:
            raise self._raise_on_refresh
        self._current = self._after_refresh
        return self._after_refresh


def _register_response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://hub/register"),
                          json=payload)


class _ScriptedHttp:
    """Answers one queued ``httpx.Response`` per ``post()`` call; records the
    bearer token sent each time."""

    def __init__(self, responses: list[httpx.Response]):
        self._responses = list(responses)
        self.tokens_used: list[str] = []

    def post(self, url, *, json=None, headers=None):
        self.tokens_used.append(headers["Authorization"].removeprefix("Bearer "))
        return self._responses.pop(0)


def test_register_without_token_source_uses_the_passed_token_with_no_retry():
    # Exactly today's behavior when no token_source is wired in: the passed
    # token= is used as-is, and a 401 is NOT retried -- it just raises.
    http = _ScriptedHttp([_register_response(401)])
    client = HubClient("https://hub", http=http)
    with pytest.raises(httpx.HTTPStatusError):
        client.register(token="stale", reference={}, manifest={}, evidence={})
    assert http.tokens_used == ["stale"]


def test_register_with_token_source_uses_current_token_on_success():
    http = _ScriptedHttp([_register_response(200, {"solution_id": "x"})])
    source = _FakeTokenSource(current="fresh")
    client = HubClient("https://hub", http=http, token_source=source)

    result = client.register(reference={}, manifest={}, evidence={})

    assert result == {"solution_id": "x"}
    assert http.tokens_used == ["fresh"]
    assert source.refresh_calls == 0


def test_register_retries_once_after_401_and_succeeds():
    http = _ScriptedHttp([_register_response(401), _register_response(200, {"solution_id": "x"})])
    source = _FakeTokenSource(current="stale", after_refresh="fresh")
    client = HubClient("https://hub", http=http, token_source=source)

    result = client.register(reference={}, manifest={}, evidence={})

    assert result == {"solution_id": "x"}
    assert http.tokens_used == ["stale", "fresh"]
    assert source.refresh_calls == 1


def test_register_raises_autherror_when_still_401_after_refresh():
    http = _ScriptedHttp([_register_response(401), _register_response(401)])
    source = _FakeTokenSource(current="stale", after_refresh="also-stale")
    client = HubClient("https://hub", http=http, token_source=source)

    with pytest.raises(AuthError, match="rejected the token even after refresh"):
        client.register(reference={}, manifest={}, evidence={})
    assert http.tokens_used == ["stale", "also-stale"]
    assert source.refresh_calls == 1  # exactly one retry, never a loop


def test_register_propagates_non_401_errors_without_touching_the_source():
    http = _ScriptedHttp([_register_response(500)])
    source = _FakeTokenSource(current="fresh")
    client = HubClient("https://hub", http=http, token_source=source)

    with pytest.raises(httpx.HTTPStatusError):
        client.register(reference={}, manifest={}, evidence={})
    assert source.refresh_calls == 0


def test_register_propagates_autherror_when_the_reactive_refresh_itself_fails():
    http = _ScriptedHttp([_register_response(401)])
    source = _FakeTokenSource(current="stale", raise_on_refresh=AuthError("no refresh token"))
    client = HubClient("https://hub", http=http, token_source=source)

    with pytest.raises(AuthError, match="no refresh token"):
        client.register(reference={}, manifest={}, evidence={})


def test_base_url_trailing_slash_is_stripped():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000/", http=http)

    client.objectives()

    assert http.calls[0][1] == "http://localhost:8000/objectives"


# --- Property 2: device_login's GitHub device flow -------------------------
#
# device_login() runs the GitHub device flow and returns the user token set
# ({access_token, refresh_token, expires_in}); `nethackers login` calls it and
# persists the result. refresh_access_token() renews an expired token set
# without a client secret (the device-flow allowance).


def test_device_login_returns_access_token():
    calls = []

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    class FakeHttp:
        def __init__(self):
            self.n = 0

        def post(self, url, data, headers):
            if url.endswith("/device/code"):
                return FakeResp(
                    {
                        "verification_uri": "https://gh/dev",
                        "user_code": "WXYZ",
                        "device_code": "dc",
                        "interval": 0,
                    }
                )
            self.n += 1
            if self.n == 1:
                return FakeResp({"error": "authorization_pending"})
            return FakeResp({"access_token": "gho_realtoken"})

    prompts = []
    token = device_login(http=FakeHttp(), prompt=lambda uri, code: prompts.append((uri, code)),
                         sleep=lambda _s: calls.append(1))
    # device_login now returns the full token set, not a bare string.
    assert token["access_token"] == "gho_realtoken"
    assert any(code == "WXYZ" for _uri, code in prompts)  # user shown the code


# --- Property 2b: full token set + silent refresh --------------------------
#
# device_login now returns {"access_token", "refresh_token"|None,
# "expires_in"|None} so the CLI can persist a refreshable credential, and
# refresh_access_token exchanges a stored refresh token for a fresh set
# without a new device prompt (raising DeviceFlowError on a terminal error).


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_device_login_returns_full_token_set():
    seq = [
        _FakeResp(
            {"device_code": "d", "user_code": "WDJB-MJHT", "verification_uri": "u", "interval": 0}
        ),
        _FakeResp({"error": "authorization_pending"}),
        _FakeResp({"access_token": "ghu_x", "refresh_token": "ghr_y", "expires_in": 28800}),
    ]
    calls = iter(seq)

    class H:
        def post(self, url, data=None, headers=None):
            return next(calls)

    out = r.device_login(client_id="cid", http=H(), prompt=lambda *_: None, sleep=lambda *_: None)
    assert out == {"access_token": "ghu_x", "refresh_token": "ghr_y", "expires_in": 28800}


def test_refresh_access_token():
    class H:
        def post(self, url, data=None, headers=None):
            assert data["grant_type"] == "refresh_token"
            return _FakeResp(
                {"access_token": "ghu_new", "refresh_token": "ghr_new", "expires_in": 28800}
            )

    out = r.refresh_access_token("ghr_old", client_id="cid", http=H())
    assert out["access_token"] == "ghu_new"


def test_refresh_error_raises():
    class H:
        def post(self, url, data=None, headers=None):
            return _FakeResp({"error": "bad_refresh_token"})

    with pytest.raises(r.DeviceFlowError):
        r.refresh_access_token("ghr_old", client_id="cid", http=H())
