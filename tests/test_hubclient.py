"""Tests for ``nethackers.hubclient.client.HubClient`` and
``nethackers.hubclient.register.register_solution`` -- both exercised
against fake ``http`` doubles that record calls and return canned
responses, never real ``httpx``/a real network call. ``HubClient``'s
methods are checked against Task 12's actual API surface
(``nethackers.hub.api``): same paths, same ``?param=`` names, the register
body's link shape, and the register ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import pytest

from nethackers.hubclient import register as r
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
        token="t", repo="github.com/sam/nethacker", commit="c" * 40, root="bot"
    )
    assert sent["json"] == {
        "reference": {"repo": "github.com/sam/nethacker", "commit": "c" * 40},
        "root": "bot",
    }
    assert sent["headers"]["Authorization"] == "Bearer t"


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
    token = device_login(http=FakeHttp(), prompt=prompts.append, sleep=lambda _s: calls.append(1))
    # device_login now returns the full token set, not a bare string.
    assert token["access_token"] == "gho_realtoken"
    assert any("WXYZ" in p for p in prompts)  # user shown the code


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
