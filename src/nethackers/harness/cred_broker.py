"""Host-side credential broker (spec §3d, INV2). Holds the model key and
injects it only into calls forwarded to the provider; the mutator container
is given the broker's URL and a PLACEHOLDER key, so untrusted code in the box
never sees the real credential and can reach only the provider through this
hop.

This is a reverse proxy to exactly ONE upstream, fixed at construction time
-- it never proxies to a host of the request's choosing. Two checks keep
that true. The forward URL is ``upstream_base`` + the request-target, so
``_proxy`` accepts only an origin-form target (one starting with ``/``): a
target such as ``@evil.example/v1/messages`` would otherwise re-home the
URL (the upstream host becomes userinfo) and carry the injected credential
to a host the client picked. The ``Host`` check doesn't select where a
request goes either; it's a defense-in-depth refusal of anything that
doesn't even look like it's addressed to this broker or its one upstream,
so a future change to this file (or a bug in it) has one fewer way to turn
this into an open relay.
"""
from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx

# Methods a provider's HTTP API might reasonably receive. The two tests only
# exercise GET/POST, but a real provider API is not GET/POST-only (e.g. some
# expose DELETE for batch/job cancellation), and every verb runs the exact
# same, already-tested `_proxy` body -- so covering them costs nothing extra.
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# The broker picks a free port from this FIXED range (not an ephemeral OS
# port) so a host firewall rule can be scoped to exactly these ports. On
# native Linux the sandbox reaches the broker via the docker bridge gateway,
# which a locked-down firewall (ufw) blocks by default; a scoped
# `ufw allow in on docker0 to <gateway> port 11700:11749 proto tcp` opens ONLY
# the broker's ports -- not every host service -- which a blanket
# `allow in on docker0` would. 50 ports covers far more concurrent broker
# processes than a single host realistically runs (each evolve run starts one
# broker per brokered provider). See container_operator.ufw_rule_hint.
BROKER_PORT_RANGE = range(11700, 11750)

# Response headers that describe the upstream's wire framing rather than the
# content itself. Dropped because this broker streams the forward library's
# already content-decoded bytes -- `httpx`'s `iter_bytes()` normally, or (see
# `impersonate` below) `curl_cffi`'s `iter_content()`, which decodes gzip/br/
# deflate the same way (libcurl's `CURLOPT_ACCEPT_ENCODING` auto-decompresses
# before the callback that fills it ever sees the bytes) -- never the raw
# wire bytes, either way, to the client. Forwarding the upstream's own
# framing/encoding headers verbatim would describe bytes that no longer match
# what's actually written (e.g. a stale `Content-Encoding: gzip` once the
# body's already been decompressed, or a fixed Content-Length/chunked
# Transfer-Encoding that doesn't match this connection-close-delimited
# stream).
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
    starts the proxy and yields its base URL; the block's exit stops it.

    ``impersonate`` (default ``False``) is the ONLY thing that changes what
    forwards a request -- the header-rewrite logic above is shared, transport-
    agnostic, and untouched either way. ``False`` (claude, opencode2) keeps
    the plain ``httpx`` forward. ``True`` (codex) forwards via ``curl_cffi``
    with Chrome TLS impersonation instead: codex's upstream, ``chatgpt.com``,
    sits behind Cloudflare JA3/TLS fingerprinting that 403s a plain ``httpx``
    request, so this hop needs a client that impersonates a real browser's TLS
    handshake, not just its headers. ``curl_cffi`` is never a packaged
    dependency (the mutator image/fingerprint and ``uv.lock`` stay untouched);
    it is installed host-side on demand instead (``harness.impersonation``):
    ``nethackers setup`` pre-installs it for codex, and ``start`` self-heals on
    first use if setup was skipped.
    """

    def __init__(
        self, upstream_base: str, rewrite: HeaderRewrite, impersonate: bool = False,
        *, bind_host: str = "127.0.0.1",
    ) -> None:
        self._upstream = upstream_base.rstrip("/")
        self._upstream_host = urlsplit(self._upstream).hostname
        self._rewrite = rewrite
        self._impersonate = impersonate
        # Interface the proxy listens on. Default 127.0.0.1 is right on macOS
        # (Docker Desktop routes the container's `host.docker.internal` to the
        # host loopback). On native Linux the container reaches the host via the
        # docker BRIDGE GATEWAY (e.g. 172.17.0.1), which can't reach a loopback
        # listener, so the ContainerOperator binds the gateway IP instead --
        # reachable from the sandbox, NOT the host's public interface (never
        # 0.0.0.0). See container_operator._start_broker_auth.
        self._bind_host = bind_host
        # Requests that reached the proxy (any method/status). Zero after a
        # broker run means the sandbox never reached the broker at all -- the
        # fail-loud signal ContainerOperator turns into a firewall hint
        # (docker0->host blocked, e.g. by ufw) instead of a confusing agent
        # timeout.
        self.requests_seen = 0
        self._client: httpx.Client | None = None
        # Type is `Any`: `curl_cffi` is an optional, lazily-imported dep
        # (never installed for mypy/tests to see -- see `start`), so its
        # `Session` type is never available to annotate with here.
        self._cffi_session: Any = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> str:
        broker = self
        # Always constructed, even when `impersonate` is True and this
        # client goes unused by `_proxy` below: it's unopened until a
        # request is actually made (no socket, no handshake), so the
        # alternative -- an `httpx.Client | None` -- would only buy `_proxy`
        # an extra `is not None` narrowing for no real benefit.
        client = httpx.Client(timeout=600.0)
        self._client = client
        cffi_session: Any = None
        if self._impersonate:
            # Host-side-only, installed on demand: curl_cffi is never a packaged
            # dependency (the mutator image/fingerprint and uv.lock stay
            # untouched), so `load_impersonate_session` self-installs it into
            # THIS interpreter on first codex use when `nethackers setup` didn't
            # already. Only the codex broker sets `impersonate`, so only a codex
            # run reaches this branch. Chrome impersonation matches the JA3/TLS
            # fingerprint Cloudflare allow-lists (a real codex CLI negotiates as
            # some browser-shaped TLS client, not bare httpx/urllib3 -- "chrome"
            # is curl_cffi's best-supported target, not a claim about codex's own
            # User-Agent, which is forwarded unchanged regardless).
            from nethackers.harness.impersonation import load_impersonate_session
            cffi_session = load_impersonate_session("chrome")
            self._cffi_session = cffi_session

        class Handler(BaseHTTPRequestHandler):
            def _proxy(self) -> None:
                broker.requests_seen += 1
                if not broker._host_allowed(self.headers.get("Host")):
                    self.send_response(403)
                    self.end_headers()
                    return
                # Origin-form only. `url` below is `_upstream + self.path`,
                # and a target that does not start with "/" (e.g.
                # "@evil.example/v1/messages") lands in the URL's authority:
                # the upstream host turns into userinfo and the request, with
                # the real credential injected, goes to the client's host.
                # A real CLI only ever sends origin-form, so this refuses
                # nothing legitimate.
                if not self.path.startswith("/"):
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
                url = broker._upstream + self.path
                try:
                    if broker._impersonate:
                        # curl_cffi has no context-manager protocol on its
                        # `Response` (unlike httpx's `client.stream(...)`), so
                        # the `finally: r.close()` below is what releases the
                        # streaming handle instead -- on every exit, success
                        # or exception alike.
                        r = cffi_session.request(
                            self.command, url, data=body, headers=headers, stream=True,
                        )
                        try:
                            self.send_response(r.status_code)
                            for k, v in r.headers.items():
                                if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS:
                                    self.send_header(k, v)
                            self.close_connection = True
                            self.end_headers()
                            # curl_cffi's `iter_content()` -- like httpx's
                            # `iter_bytes()` above -- yields already
                            # content-decoded bytes: curl_cffi asks libcurl to
                            # auto-decompress (`CURLOPT_ACCEPT_ENCODING`) via
                            # its default `accept_encoding="gzip, deflate,
                            # br"`, so what lands here is never raw
                            # gzip/br/deflate wire bytes, matching the
                            # `Content-Encoding` strip in
                            # `_HOP_BY_HOP_RESPONSE_HEADERS` above exactly the
                            # same way the httpx branch needs it to.
                            for chunk in r.iter_content():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                        finally:
                            r.close()
                    else:
                        with client.stream(
                            self.command, url,
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
                            for chunk in up.iter_bytes():
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

        server: ThreadingHTTPServer | None = None
        for candidate in BROKER_PORT_RANGE:
            try:
                server = ThreadingHTTPServer((self._bind_host, candidate), Handler)
                break
            except OSError:
                continue   # port busy (a concurrent broker, or something else) -- try the next
        if server is None:
            # Range exhausted (more concurrent brokers than the window). Fall
            # back to an ephemeral port so the broker still starts; on a ufw
            # host that port is outside the scoped rule, so the run then fails
            # LOUD (firewall hint) rather than silently not starting at all.
            server = ThreadingHTTPServer((self._bind_host, 0), Handler)
        self._server = server
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
        return f"http://{self._bind_host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._client is not None:
            self._client.close()
        if self._cffi_session is not None:
            self._cffi_session.close()

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
