"""Tests for the host-side credential broker (spec §3d, INV2).

``fake_upstream`` isn't a shared fixture anywhere in this repo, so this
module builds one inline: a second stdlib ``ThreadingHTTPServer`` standing in
for the model provider, recording the headers of the last request it
received and answering 200 with a JSON body that echoes the request back.
It only ever talks to the broker under test on loopback -- nothing here
reaches a real network.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest

from nethackers.harness.cred_broker import CredBroker, HeaderRewrite

# A handful of SSE-style events the fake upstream's `/sse` endpoint writes
# one at a time (see `_respond_sse`) rather than as a single blob -- shared
# with the test so the expected concatenation can't drift from what the
# handler actually sends.
_SSE_EVENTS = tuple(f"data: {i}\n\n" for i in range(5))


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Records the last request's headers (lower-cased) and answers 200 with
    a JSON body that echoes the method/path/request-body back -- a normal
    round-trip response shaped by the actual request, not a fixed stub, so a
    test asserting the real key never comes back in the response is checking
    something that could plausibly contain it if the broker's response path
    ever broke, rather than a constant that never could either way."""

    def _respond(self) -> None:
        self.server.owner.last_headers = {  # type: ignore[attr-defined]
            k.lower(): v for k, v in self.headers.items()}
        length = int(self.headers.get("Content-Length") or 0)
        received = self.rfile.read(length) if length else b""
        body = json.dumps({
            "ok": True,
            "method": self.command,
            "path": self.path,
            "echo": received.decode("utf-8", "replace"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_sse(self) -> None:
        # Writes its body in pieces, flushing and pausing between them, so
        # a request to `/sse` is genuinely produced over time -- unlike
        # `_respond` above, which writes its whole (short) body in one shot
        # and so can't tell a streaming broker apart from a buffering one.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in _SSE_EVENTS:
            self.wfile.write(event.encode())
            self.wfile.flush()
            time.sleep(0.02)

    def do_GET(self) -> None:
        if self.path == "/sse":
            self._respond_sse()
        else:
            self._respond()

    def do_POST(self) -> None:
        if self.path == "/sse":
            self._respond_sse()
        else:
            self._respond()

    def log_message(self, *args: object) -> None:  # quiet the test output
        pass


class _FakeUpstream:
    """A stand-in model provider: its own server on an ephemeral port, plus
    ``last_headers`` for the test to assert on and ``url`` for the broker to
    forward to."""

    def __init__(self) -> None:
        self.last_headers: dict[str, str] | None = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstreamHandler)
        self._server.daemon_threads = True
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


@pytest.fixture
def fake_upstream():
    upstream = _FakeUpstream()
    try:
        yield upstream
    finally:
        upstream.stop()


def test_broker_injects_auth_and_forwards(fake_upstream):
    rewrite = HeaderRewrite(inject=(("Authorization", "Bearer REALKEY"),))
    with CredBroker(upstream_base=fake_upstream.url, rewrite=rewrite) as base:
        r = httpx.post(f"{base}/v1/messages", json={"hi": 1},
                        headers={"Authorization": "Bearer PLACEHOLDER"})
    assert r.status_code == 200
    # the real key was injected host-side; the client-supplied placeholder
    # was overwritten before the request ever left the host.
    assert fake_upstream.last_headers["authorization"] == "Bearer REALKEY"
    # The invariant this module exists for: the real key must never flow
    # back out to the (untrusted) client in the response -- neither as a
    # header value nor inside the body. `_proxy` only ever copies the
    # UPSTREAM response's headers/content back to the client; it has no
    # code path that touches the outgoing request's injected auth header
    # again once the forward has been made. The fake upstream's response
    # echoes real request-derived content (method/path/body), so this is
    # exercising that echoed content, not a tautology against a constant.
    assert "REALKEY" not in r.text
    assert all("REALKEY" not in v for v in r.headers.values())


def test_broker_refuses_offhost(fake_upstream):
    rewrite = HeaderRewrite(inject=(("Authorization", "Bearer REALKEY"),))
    with CredBroker(fake_upstream.url, rewrite) as base:
        parsed = urlsplit(base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            # httpx always derives the Host header from the request URL, so
            # forcing an off-host Host means dropping to http.client and
            # setting it explicitly on the wire instead.
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", "evil.example")
            conn.endheaders()
            status = conn.getresponse().status
        finally:
            conn.close()
    assert status == 403


def test_broker_allows_host_docker_internal(fake_upstream):
    # The mutator container reaches the broker via `host.docker.internal`
    # (ContainerOperator's broker path: `--add-host host.docker.internal:
    # host-gateway` + ANTHROPIC_BASE_URL/OPENAI_BASE_URL pointed at the
    # broker -- see container_operator.py), so every real broker-path
    # request arrives with `Host: host.docker.internal`. That must be
    # ALLOWED (forwarded, like the broker's own loopback addresses), not
    # rejected as a genuine off-host guess the way "evil.example" is above.
    rewrite = HeaderRewrite(inject=(("Authorization", "Bearer REALKEY"),))
    with CredBroker(fake_upstream.url, rewrite) as base:
        parsed = urlsplit(base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", "host.docker.internal")
            conn.endheaders()
            response = conn.getresponse()
            status = response.status
            response.read()
        finally:
            conn.close()
    assert status == 200
    assert fake_upstream.last_headers["authorization"] == "Bearer REALKEY"


# --- HeaderRewrite ops: inject / strip / merge_csv --------------------------


def test_broker_inject_replaces_inbound_header_case_insensitively(fake_upstream):
    # The client's own header can arrive in ANY case (RFC 9110 S:4.2); the
    # rewrite's `inject` must still be the only one of that name upstream
    # sees -- the client's differently-cased placeholder must not survive
    # alongside it.
    rewrite = HeaderRewrite(inject=(("Authorization", "Bearer REALKEY"),))
    with CredBroker(fake_upstream.url, rewrite) as base:
        parsed = urlsplit(base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            conn.putrequest("POST", "/v1/messages")
            conn.putheader("AUTHORIZATION", "Bearer client-placeholder-lower")
            conn.putheader("Content-Length", "0")
            conn.endheaders()
            response = conn.getresponse()
            status = response.status
            response.read()
        finally:
            conn.close()
    assert status == 200
    assert fake_upstream.last_headers["authorization"] == "Bearer REALKEY"
    assert "Bearer client-placeholder-lower" not in fake_upstream.last_headers.values()


def test_broker_strip_removes_inbound_header(fake_upstream):
    rewrite = HeaderRewrite(strip=("x-api-key",))
    with CredBroker(fake_upstream.url, rewrite) as base:
        r = httpx.post(f"{base}/v1/messages", json={"hi": 1},
                        headers={"x-api-key": "leak"})
    assert r.status_code == 200
    assert "x-api-key" not in fake_upstream.last_headers
    assert "leak" not in fake_upstream.last_headers.values()


def test_broker_merge_csv_unions_client_values_and_dedupes(fake_upstream):
    # merge_csv must keep the client's own beta flag ("foo") AND add the
    # broker's ("oauth-2025-04-20") -- even though the client ALSO already
    # sent the broker's value once, it must not be duplicated.
    rewrite = HeaderRewrite(merge_csv=(("anthropic-beta", ("oauth-2025-04-20",)),))
    with CredBroker(fake_upstream.url, rewrite) as base:
        r = httpx.post(f"{base}/v1/messages", json={"hi": 1},
                        headers={"anthropic-beta": "foo, oauth-2025-04-20"})
    assert r.status_code == 200
    assert fake_upstream.last_headers["anthropic-beta"] == "foo, oauth-2025-04-20"


# --- Streaming passthrough ---------------------------------------------


def test_broker_streams_sse_without_buffering(fake_upstream):
    # `_respond_sse` writes `_SSE_EVENTS` one at a time, flushing and
    # pausing between them -- not as a single blob. A broker that still
    # buffered the whole response (e.g. via `.content`) before replying
    # would produce the same concatenated text here (that's (a)), but it
    # would also know the total size up front and send a Content-Length
    # header for it. (b) is the actual proof this response was streamed
    # rather than buffered whole.
    rewrite = HeaderRewrite()
    with CredBroker(fake_upstream.url, rewrite) as base:
        r = httpx.get(f"{base}/sse")
    assert r.status_code == 200
    # (a) every chunk arrived, in order, concatenated back into the exact
    # original text -- streaming didn't drop or reorder any bytes.
    assert r.text == "".join(_SSE_EVENTS)
    # (b) no Content-Length: the broker couldn't have known the total size
    # up front, because it never held the whole response at once.
    assert "content-length" not in r.headers
