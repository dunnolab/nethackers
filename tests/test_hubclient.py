"""Tests for ``nethackers.hubclient.client.HubClient`` and
``nethackers.hubclient.register.register_solution`` -- both exercised
against fake ``http`` doubles that record calls and return canned
responses, never real ``httpx``/a real network call. ``HubClient``'s
methods are checked against Task 12's actual API surface
(``nethackers.hub.api``): same paths, same ``?param=`` names, the register
body's three keys, and the register ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import pytest

from nethackers.hubclient.client import HubClient
from nethackers.hubclient.register import DeviceFlowError, device_login, register_solution


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


def test_register_posts_bearer_header_and_three_key_body():
    http = _FakeHttp(response={"solution_digest": "sha256:x"})
    client = HubClient("http://localhost:8000", http=http)
    reference = {"repo": "github.com/sam/x", "commit": "a" * 40}
    manifest = {"root": ".", "entrypoint": "bot.py"}
    evidence = {"solution_digest": "sha256:x"}

    result = client.register(
        token="tok-sam", reference=reference, manifest=manifest, evidence=evidence
    )

    assert result == {"solution_digest": "sha256:x"}
    assert http.calls == [
        (
            "POST",
            "http://localhost:8000/register",
            {"reference": reference, "manifest": manifest, "evidence": evidence},
            {"Authorization": "Bearer tok-sam"},
        )
    ]


def test_base_url_trailing_slash_is_stripped():
    http = _FakeHttp(response=[])
    client = HubClient("http://localhost:8000/", http=http)

    client.objectives()

    assert http.calls[0][1] == "http://localhost:8000/objectives"


# --- Property 2: device_login's GitHub device flow -------------------------
#
# device_login() is the device-flow half extracted out of register_solution
# (Task 5) so a future `nethackers login` verb can call it directly.
# register_solution now just calls device_login() then hub.register() -- its
# own tests below (Property 3) still exercise the whole thing end-to-end and
# must keep passing unchanged.


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
    assert token == "gho_realtoken"
    assert any("WXYZ" in p for p in prompts)  # user shown the code


# --- Property 3: register_solution's device flow ---------------------------

_DEVICE_CODE_RESPONSE = {
    "device_code": "devcode123",
    "user_code": "ABCD-1234",
    "verification_uri": "https://github.com/login/device",
    "interval": 5,
}


class _FakeDeviceHttp:
    """Scripts a sequence of POST responses (device-code call, then each
    token poll) and records every posted body/headers."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.posts: list[tuple[str, dict, dict]] = []

    def post(self, url, *, data=None, headers=None):
        self.posts.append((url, data, headers))
        return _FakeResponse(self._responses.pop(0))


class _FakeHub:
    """Records every ``register`` call; returns a canned result."""

    def __init__(self, result=None):
        self.calls: list[dict] = []
        self._result = result if result is not None else {"solution_digest": "sha256:x"}

    def register(self, *, token, reference, manifest, evidence):
        self.calls.append(
            {"token": token, "reference": reference, "manifest": manifest, "evidence": evidence}
        )
        return self._result


def test_register_solution_polls_on_pending_then_registers():
    http = _FakeDeviceHttp(
        [_DEVICE_CODE_RESPONSE, {"error": "authorization_pending"}, {"access_token": "gho_x"}]
    )
    hub = _FakeHub(result={"solution_digest": "sha256:x", "owner": "sam"})
    prompts: list[str] = []
    sleeps: list[float] = []
    reference = {"repo": "github.com/sam/x", "commit": "a" * 40}
    manifest = {"root": "."}
    evidence = {"solution_digest": "sha256:x"}

    result = register_solution(
        hub=hub,
        reference=reference,
        manifest=manifest,
        evidence=evidence,
        http=http,
        prompt=prompts.append,
        sleep=sleeps.append,
    )

    assert result == {"solution_digest": "sha256:x", "owner": "sam"}
    # Polling: authorization_pending must have triggered exactly one sleep,
    # using the device response's own interval.
    assert sleeps == [5]
    # The prompt shown to the user carries both the verification URL and
    # the user code from the device-code response.
    assert len(prompts) == 1
    assert "https://github.com/login/device" in prompts[0]
    assert "ABCD-1234" in prompts[0]
    # hub.register only ever sees the resulting access token, plus the
    # reference/manifest/evidence passed straight through unchanged.
    assert hub.calls == [
        {"token": "gho_x", "reference": reference, "manifest": manifest, "evidence": evidence}
    ]
    # 1 device-code POST + 2 token polls (pending, then success).
    assert len(http.posts) == 3


def test_register_solution_terminal_error_raises_device_flow_error():
    http = _FakeDeviceHttp([_DEVICE_CODE_RESPONSE, {"error": "access_denied"}])
    hub = _FakeHub()

    with pytest.raises(DeviceFlowError):
        register_solution(
            hub=hub,
            reference={},
            manifest={},
            evidence={},
            http=http,
            prompt=lambda _msg: None,
            sleep=lambda _seconds: None,
        )

    # A terminal error must never reach the hub.
    assert hub.calls == []
