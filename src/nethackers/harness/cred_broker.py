"""Host-side credential broker (spec §3d, INV2). Holds the model key and
injects it only into calls forwarded to the provider; the mutator container
is given the broker's URL and a PLACEHOLDER key, so untrusted code in the box
never sees the real credential and can reach only the provider through this
hop.

This is a reverse proxy to exactly ONE upstream, fixed at construction time
-- it never proxies to a host of the request's choosing. The ``Host`` check
in ``_proxy`` doesn't select where a request goes (that's always
``upstream_base``); it's a defense-in-depth refusal of anything that doesn't
even look like it's addressed to this broker or its one upstream, so a
future change to this file (or a bug in it) has one fewer way to turn this
into an open relay.
"""
from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx

# Methods a provider's HTTP API might reasonably receive. The two tests only
# exercise GET/POST, but a real provider API is not GET/POST-only (e.g. some
# expose DELETE for batch/job cancellation), and every verb runs the exact
# same, already-tested `_proxy` body -- so covering them costs nothing extra.
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Response headers that describe the upstream's wire framing rather than the
# content itself. Dropped and recomputed (or simply omitted) because this
# broker reconstructs the response from `httpx`'s already-decoded `r.content`
# -- forwarding them verbatim could describe bytes that no longer match what
# gets written (e.g. a Content-Length measured before gzip decoding).
_HOP_BY_HOP_RESPONSE_HEADERS = frozenset(
    {"transfer-encoding", "content-encoding", "connection", "content-length"}
)


@dataclass(frozen=True)
class HeaderRewrite:
    """A declarative header rewrite applied to every request this broker
    forwards -- the single seam every operator (Claude, Codex, OpenCode)
    maps onto, so ``CredBroker`` itself never carries per-operator logic.

    - ``inject``: ``(name, value)`` pairs set on the outgoing request,
      REPLACING any inbound header of that name (case-insensitively) so a
      client-supplied placeholder can never shadow the real value.
    - ``strip``: inbound header names dropped outright (e.g. a stray
      ``x-api-key`` that would otherwise sit alongside an injected
      ``Authorization``).
    - ``merge_csv``: ``(name, values)`` pairs whose ``values`` are unioned
      into a comma-list header, preserving the client's own values and
      order, deduping so a value present in both never repeats.
    """

    inject: tuple[tuple[str, str], ...] = ()
    strip: tuple[str, ...] = ()
    merge_csv: tuple[tuple[str, tuple[str, ...]], ...] = ()


class CredBroker:
    """Context manager: ``with CredBroker(upstream, rewrite) as base:``
    starts the proxy and yields its base URL; the block's exit stops it."""

    def __init__(self, upstream_base: str, rewrite: HeaderRewrite) -> None:
        self._upstream = upstream_base.rstrip("/")
        self._upstream_host = urlsplit(self._upstream).hostname
        self._rewrite = rewrite
        self._client: httpx.Client | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> str:
        broker = self
        client = httpx.Client(timeout=600.0)
        self._client = client

        class Handler(BaseHTTPRequestHandler):
            def _proxy(self) -> None:
                if not broker._host_allowed(self.headers.get("Host")):
                    self.send_response(403)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                rewrite = broker._rewrite
                drop = {"host", "content-length", *(n.lower() for n in rewrite.strip),
                        *(n.lower() for n, _ in rewrite.inject),
                        *(n.lower() for n, _ in rewrite.merge_csv)}
                headers = {k: v for k, v in self.headers.items() if k.lower() not in drop}
                for name, value in rewrite.inject:
                    headers[name] = value
                for name, values in rewrite.merge_csv:
                    existing = [p.strip() for p in (self.headers.get(name) or "").split(",")
                                if p.strip()]
                    headers[name] = ", ".join(dict.fromkeys([*existing, *values]))
                try:
                    with client.stream(
                        self.command, broker._upstream + self.path,
                        content=body, headers=headers,
                    ) as up:
                        self.send_response(up.status_code)
                        for k, v in up.headers.items():
                            if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS:
                                self.send_header(k, v)
                        # No Content-Length: the body is streamed to the
                        # client as it arrives from upstream (never fully
                        # buffered here), so the total size isn't known up
                        # front. Closing the connection once this response
                        # ends is what tells the client where the body
                        # stops instead.
                        self.close_connection = True
                        self.end_headers()
                        for chunk in up.iter_raw():
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except Exception:
                    # `client` can be closed out from under this thread by a
                    # concurrent stop() (daemon_threads=True means stop()
                    # doesn't wait for an in-flight request -- see the
                    # comment below), the upstream can be unreachable, or
                    # the client side of THIS connection can drop mid-write.
                    # Whatever the cause, a forwarding failure must end the
                    # request cleanly rather than raise out of the handler
                    # (socketserver's default error handling would otherwise
                    # print a stack trace to stderr for what is, from the
                    # mutator's point of view, just a failed HTTP call).
                    # Best-effort only: if the client side is what's gone,
                    # this send fails too and is swallowed rather than
                    # raising a second exception.
                    with contextlib.suppress(Exception):
                        self.send_response(502)
                        self.end_headers()

            def log_message(self, *args: object) -> None:  # quiet; no test/prod need
                pass

        for method in _METHODS:
            setattr(Handler, f"do_{method}", Handler._proxy)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # DELIBERATE: stop() joins the accept-loop thread (`self._thread`,
        # below) so `serve_forever` is guaranteed to have exited, but it
        # does NOT wait for whatever per-request worker thread ThreadingMixIn
        # spawned for a request that's still in flight -- draining those
        # would mean stop() could hang on a stuck/slow request (e.g. the
        # harness killing a hung mutator container mid-call). daemon_threads
        # = True means an in-flight worker thread can't block process exit
        # either way; the try/except in `_proxy` above is what makes an
        # interrupted in-flight request fail cleanly (a 502, or just a
        # closed connection) instead of raising once `stop()` has closed
        # `client`/the socket out from under it.
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._client is not None:
            self._client.close()

    def _host_allowed(self, host_header: str | None) -> bool:
        # Host headers are case-insensitive (RFC 9110 §4.2.3); `.hostname`
        # already lower-cases `self._upstream_host`, so the incoming side
        # must be lower-cased too or a same-host request in a different case
        # (e.g. "LOCALHOST") would be wrongly refused.
        #
        # `host.docker.internal` is the mutator container's OWN route back to
        # this broker -- ContainerOperator's broker path adds `--add-host
        # host.docker.internal:host-gateway` and points the harness's
        # base-URL env at exactly that host (container_operator.py), so every
        # broker-path request genuinely arrives with this Host header. It
        # belongs alongside the broker's own loopback addresses, not with a
        # real off-host guess like "evil.example".
        host = (host_header or "").split(":")[0].lower()
        return not host or host in (
            "127.0.0.1", "localhost", "host.docker.internal", self._upstream_host,
        )
