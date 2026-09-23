"""Broker-transform E2E tests (Task 6, design §4.2): for each operator, a
REAL ``CredBroker`` sits between a client sending operator-shaped requests
and a ``MockProvider`` standing in for the real model provider -- proving
each operator's wire contract (real credential injected, placeholder gone,
stray headers stripped/merged, `ChatGPT-Account-Id` present, path + body
untouched) WITHOUT a container. No Docker, no real network; runs in CI.

Every operator's ``HeaderRewrite`` here is the REAL one --
``broker_credential`` / ``opencode2_broker_targets`` -- built from
``tmp_path`` fakes, never the real ``~/.claude`` or ``~/.codex``: this file
never touches a host login and never prints a real token. Every credential
value below is an obvious fake (``REAL-CLAUDE``, ``sk-ant-real``, ...), never
an API key -- subscription/OAuth shapes only, per the broker's own contract.

Left UNMARKED (no ``broker_e2e``/``e2e`` marker): everything here is a local
loopback HTTP server plus a real ``CredBroker``, no container and no real
network, so it runs on every PR like any other fast unit test.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest

from nethackers.harness import auth_inject
from nethackers.harness.auth_inject import broker_credential, opencode2_broker_targets
from nethackers.harness.cred_broker import CredBroker, HeaderRewrite

from .mock_provider import MockProvider


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _far_future_jwt(**claims: object) -> str:
    """A structurally-valid, UNSIGNED (``alg: none``) JWT with an ``exp``
    years out (``header.payload.`` -- never a real token), far enough that
    ``_codex_token_needs_refresh`` never fires and no refresh network call is
    attempted."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    far_future = int(time.time()) + 10 * 365 * 24 * 3600
    payload = _b64url(json.dumps({"exp": far_future, **claims}).encode())
    return f"{header}.{payload}."


def _write_opencode_config(home: Path, providers: dict, name: str = "opencode.json") -> None:
    """A tmp global OpenCode config (``~/.config/opencode/<name>``) carrying
    ``providers`` -- the exact shape ``opencode2_global_providers`` reads."""
    path = home / ".config" / "opencode" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": providers}))


# --- claude ----------------------------------------------------------------


def test_claude_transform_injects_bearer_strips_key_merges_beta(tmp_path):
    # The real HeaderRewrite for claude, sourced from a setup-token via
    # `environ` -- tmp_path has no ~/.claude at all, so a successful read
    # here already proves the setup-token path (not a keychain/file
    # fallback) was taken.
    rewrite = broker_credential(
        "claude", system="Linux", home=tmp_path,
        environ={"NETHACKERS_CLAUDE_SETUP_TOKEN": "REAL-CLAUDE"},
    )
    body = b'{"model":"claude-x","messages":[{"role":"user","content":"hi"}]}'

    provider = MockProvider()
    with provider as mock_url, CredBroker(mock_url, rewrite) as broker_url:
        r = httpx.post(
            f"{broker_url}/v1/messages?beta=true",
            content=body,
            headers={
                "Authorization": "Bearer PLACEHOLDER",
                "x-api-key": "leak",
                "anthropic-beta": "foo",
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    assert len(provider.requests) == 1
    req = provider.requests[0]
    # real cred injected, client's placeholder Bearer gone
    assert req["headers"]["authorization"] == "Bearer REAL-CLAUDE"
    # x-api-key stripped -- never sits alongside the injected Bearer upstream
    assert "x-api-key" not in req["headers"]
    # anthropic-beta merged: the client's own "foo" survives, ours is added
    assert req["headers"]["anthropic-beta"] == "foo, oauth-2025-04-20"
    # path + query forwarded intact
    assert req["path"] == "/v1/messages?beta=true"
    # body byte-identical
    assert req["body"] == body


# --- codex -------------------------------------------------------------


def test_codex_transform_injects_bearer_and_account_id(tmp_path, monkeypatch):
    # A far-future exp means broker_credential must inject the token AS-IS
    # and never refresh -- guarded explicitly below, since a refresh here
    # would be a live network POST from inside what must stay a CI unit test.
    access = _far_future_jwt()
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "",
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": access,
            "refresh_token": "r",
            "account_id": "acct-xyz",
        },
    }))

    def _no_refresh(home):
        raise AssertionError("codex refresh must not run for a fresh token")
    monkeypatch.setattr(auth_inject, "_codex_refresh", _no_refresh)

    rewrite = broker_credential("codex", system="Linux", home=tmp_path)
    body = b'{"model":"codex-x","input":[]}'

    provider = MockProvider()
    with provider as mock_url, CredBroker(mock_url, rewrite) as broker_url:
        r = httpx.post(
            f"{broker_url}/responses",
            content=body,
            headers={
                "Authorization": "Bearer PLACEHOLDER",
                "ChatGPT-Account-Id": "placeholder-acct",
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200
    req = provider.requests[0]
    assert req["headers"]["authorization"] == f"Bearer {access}"
    assert req["headers"]["chatgpt-account-id"] == "acct-xyz"
    assert req["path"] == "/responses"
    assert req["body"] == body


# --- opencode (per-provider broker) ----------------------------------------


def test_opencode_anthropic_provider_transform_injects_x_api_key(tmp_path):
    _write_opencode_config(tmp_path, {"anthropic": {"options": {"apiKey": "sk-ant-real"}}})
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    [target] = [t for t in targets if t["name"] == "anthropic"]
    body = b'{"model":"claude","messages":[]}'

    provider = MockProvider()
    with provider as mock_url, CredBroker(mock_url, target["rewrite"]) as broker_url:
        r = httpx.post(
            f"{broker_url}/v1/messages",
            content=body,
            headers={"x-api-key": "placeholder", "Content-Type": "application/json"},
        )

    assert r.status_code == 200
    req = provider.requests[0]
    assert req["headers"]["x-api-key"] == "sk-ant-real"
    # opencode's anthropic provider key is x-api-key only -- no Authorization
    # is injected alongside it.
    assert "authorization" not in req["headers"]
    assert req["body"] == body


def test_opencode_openai_provider_transform_injects_bearer(tmp_path):
    _write_opencode_config(tmp_path, {"openai": {"options": {"apiKey": "sk-oa-real"}}})
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    [target] = [t for t in targets if t["name"] == "openai"]
    body = b'{"model":"gpt-x","messages":[]}'

    provider = MockProvider()
    with provider as mock_url, CredBroker(mock_url, target["rewrite"]) as broker_url:
        r = httpx.post(
            f"{broker_url}/v1/chat/completions",
            content=body,
            headers={"Authorization": "Bearer placeholder", "Content-Type": "application/json"},
        )

    assert r.status_code == 200
    req = provider.requests[0]
    assert req["headers"]["authorization"] == "Bearer sk-oa-real"
    assert req["body"] == body


# --- streaming + status forwarding (operator-agnostic broker behavior) -----


def test_sse_passthrough_streams_events_in_order():
    provider = MockProvider(sse_events=["a", "b", "c"])
    with provider as mock_url, CredBroker(mock_url, HeaderRewrite()) as broker_url:
        r = httpx.get(f"{broker_url}/v1/messages")
    assert r.status_code == 200
    assert r.text == "data: a\n\ndata: b\n\ndata: c\n\n"


@pytest.mark.parametrize("status", [401, 429])
def test_status_forwarded_through_broker(status):
    provider = MockProvider(status=status)
    with provider as mock_url, CredBroker(mock_url, HeaderRewrite()) as broker_url:
        r = httpx.get(f"{broker_url}/v1/messages")
    assert r.status_code == status
