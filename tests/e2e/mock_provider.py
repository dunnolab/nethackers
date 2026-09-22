"""A dependency-free mock model-provider server for the broker-transform E2E
tier (Task 6, design §4.2 / ``test_broker_transform.py``).

A real ``CredBroker`` forwards to this instead of the actual provider host,
so a test can assert on the SIDE the broker forwards to -- the exact
request that left the host, after the broker's ``HeaderRewrite`` ran --
rather than on the broker's own return value. Modeled on
``tests/test_cred_broker.py``'s inline ``_FakeUpstream`` (a second stdlib
``ThreadingHTTPServer`` standing in for the provider on loopback), but kept
here as a shared, importable fixture rather than duplicated per test file,
and extended with a settable status and canned SSE.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Methods a provider's HTTP API might reasonably receive -- mirrors
# cred_broker.py's own `_METHODS`, so nothing this broker forwards is a verb
# the mock can't even receive.
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class _MockProviderHandler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        owner.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })

        if owner.sse_events is not None:
            self.send_response(owner.status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in owner.sse_events:
                self.wfile.write(f"data: {event}\n\n".encode())
                self.wfile.flush()
            return

        payload = json.dumps(owner.json_body).encode()
        self.send_response(owner.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # quiet the test output
        pass


for _method in _METHODS:
    setattr(_MockProviderHandler, f"do_{_method}", _MockProviderHandler._handle)


class MockProvider:
    """Context manager standing in for a real model provider.

    ``with MockProvider() as base_url:`` starts a ``ThreadingHTTPServer`` on
    ``127.0.0.1:0`` in a daemon thread and returns its base URL; the block's
    exit calls ``.stop()``.

    - ``.requests``: every request received, in order --
      ``{"method", "path", "headers", "body"}`` (headers lower-cased, body
      raw bytes) -- for the test to assert the transformed contract against.
    - ``.status``: the HTTP status every response carries (default 200);
      settable any time, including mid-test, to exercise 401/429/5xx
      forwarding.
    - ``sse_events``: when given (a list of strings), every request gets an
      SSE response (``Content-Type: text/event-stream``, one
      ``data: <event>\\n\\n`` per event, flushed individually as it's
      written -- never buffered whole) instead of the canned JSON body.
    - ``json_body``: the canned JSON response body when ``sse_events`` is
      not set (default ``{"ok": True}``).
    """

    def __init__(self, *, json_body: dict | None = None,
                 sse_events: list[str] | None = None, status: int = 200) -> None:
        self.requests: list[dict] = []
        self.json_body: dict = {"ok": True} if json_body is None else json_body
        self.sse_events = sse_events
        self.status = status
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _MockProviderHandler)
        self._server.daemon_threads = True
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
