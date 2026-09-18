"""Tests for the host-side credential broker (spec §3d, INV2).

``fake_upstream`` isn't a shared fixture anywhere in this repo, so this
module builds one inline: a second stdlib ``ThreadingHTTPServer`` standing in
for the model provider, recording the headers of the last request it
received and returning a canned 200 JSON body. It only ever talks to the
broker under test on loopback -- nothing here reaches a real network.
"""
from __future__ import annotations

import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest

from nethackers.harness.cred_broker import CredBroker


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Records the last request's headers (lower-cased) and always answers
    200 with a tiny JSON body -- just enough for the broker's forwarding to
    have something real to forward to and echo back."""

    def _respond(self) -> None:
        self.server.owner.last_headers = {k.lower(): v for k, v in self.headers.items()}
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
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
    with CredBroker(upstream_base=fake_upstream.url, header_name="Authorization",
                     header_value="Bearer REALKEY") as base:
        r = httpx.post(f"{base}/v1/messages", json={"hi": 1},
                        headers={"Authorization": "Bearer PLACEHOLDER"})
    assert r.status_code == 200
    # the real key was injected host-side; the client-supplied placeholder
    # was overwritten before the request ever left the host.
    assert fake_upstream.last_headers["authorization"] == "Bearer REALKEY"


def test_broker_refuses_offhost(fake_upstream):
    with CredBroker(fake_upstream.url, "Authorization", "Bearer REALKEY") as base:
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
