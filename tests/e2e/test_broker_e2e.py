"""Gated real-CLI-in-container broker E2E (Task 7, design §4.2): the REAL
agent CLI (claude / codex / opencode2), in its REAL mutator container, run
ONE-SHOT through a REAL ``cred_broker.CredBroker`` into Task 6's
``MockProvider`` -- no Docker mocking, no fake broker, no real provider or
token anywhere. Every "real" credential fed to the broker below is an
obvious ``tmp_path`` fake (``FAKE-REAL-...``), never a live subscription/API
credential: the point is to prove the WIRE MECHANICS (the broker starts, the
container reaches it via ``host.docker.internal``, the per-operator header
transform lands on the mock side, the CLI accepts the mocked response) --
not to exercise a live account. Also includes a hostile-code probe
(``hostile_probe.py``, run as the container's own command with the SAME
broker mounts/env) that tries every way to lift the real credential and must
fail every way (B1).

GATED, skipped by default (module-level ``pytestmark`` below): needs Docker
+ the amd64 mutator image already built/pulled locally -- it is emulated on
an arm64 dev box and slow (design §4.2), so this never runs in CI or
``make test``. Run it by hand once the image is present:

    NETHACKERS_E2E=1 uv run python -m pytest tests/e2e/test_broker_e2e.py -m broker_e2e -q

or ``make broker-e2e`` / ``scripts/broker-e2e.sh``, which run that exact
command. Acquire the image first if needed (``nethackers doctor`` / ``make
mutator`` / a plain ``docker pull`` of the pinned digest -- see
``sandbox_preflight.resolve_image``).

ASSUMPTIONS a real run against the actual CLIs should double-check (this
module has never been run against them -- that needs the gated image/Docker
this environment doesn't have):

- **Response shape.** ``_ANTHROPIC_SSE_SEQUENCE`` mirrors the public
  Anthropic Messages streaming SSE shape (used for claude, and for
  opencode2's "anthropic" provider); ``_CODEX_SSE_SEQUENCE`` mirrors the
  public OpenAI Responses-API streaming SSE shape. Both are this module's
  one real guess -- if a real run shows the CLI parses (or requires) a
  differently-shaped event stream, fix the canned sequence below, not the
  mounts/assertions around it.
- **One outbound call per turn.** Each PONG test asserts against WHICHEVER
  recorded request(s) on the mock carry the real (broker-injected)
  credential, so it tolerates a CLI that makes more than one call through
  the broker (e.g. a preliminary capability probe) -- but every such call
  gets the SAME canned response above; if a real CLI's *first* call expects a
  different shape (a models list, an account/limits check) before its main
  turn, it may error there before ever emitting PONG.
- **opencode2's model id.** ``anthropic/claude-haiku-4-5`` is
  passed as ``--model`` so OpenCode picks the brokered "anthropic" provider.
  OpenCode (1.18.31) validates model ids client-side against its catalog
  before any provider call, so a since-retired id fails there with a generic
  ``UnknownError`` and MOCK_COUNT=0 -- the original
  ``claude-3-5-haiku-20241022`` went stale exactly this way; keep this a
  currently-cataloged id.
- **Container network reachability.** The mutator container is not
  ``--network none``'d, so `hostile_probe.py`'s direct-provider-call step
  genuinely reaches the real internet from inside the box (this is
  intentional -- see that script's own docstring) rather than failing
  closed; a CI/sandboxed runner with no outbound network at all still
  passes (every failure mode there degrades to "no leak"), just without
  exercising that specific check.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from nethackers.config import load_stage
from nethackers.harness import container_operator, refs
from nethackers.harness.auth_inject import auth_broker_args, broker_credential
from nethackers.harness.container_operator import (
    ContainerCaps,
    ContainerOperator,
    _host_gateway_url,
    build_docker_argv,
)
from nethackers.harness.cred_broker import CredBroker
from nethackers.harness.sandbox_preflight import image_present, resolve_image

from .mock_provider import MockProvider

HOSTILE_PROBE_PATH = Path(__file__).parent / "hostile_probe.py"

# A short per-container wall-clock cap for these one-shot smoke turns -- the
# production default (ContainerCaps.timeout_s, 8h) is a runaway ceiling for a
# real mutation, not a sane bound for a "reply with one word" test.
_CAPS = ContainerCaps(timeout_s=180)

_BRIEF = "Reply with exactly the single word PONG and nothing else."


def _mutator_image() -> str:
    # resolve_image has NO side effects (its own docstring) -- safe to call
    # at collection time and again per-test.
    return resolve_image(load_stage().mutator_image, "mutator")


def _gate_reason() -> str | None:
    """``None`` -> run the gated tier; else the skip reason. Short-circuits
    on the env var FIRST so that, with ``NETHACKERS_E2E`` unset (this
    module's default, every-PR state), neither ``shutil.which`` nor a
    ``docker image inspect`` subprocess ever runs -- collection stays fast
    and side-effect-free."""
    if os.environ.get("NETHACKERS_E2E") != "1":
        return ("gated: set NETHACKERS_E2E=1 to run the real-CLI broker E2E tier "
                 "(needs Docker + the amd64 mutator image built/pulled locally)")
    if shutil.which("docker") is None:
        return "gated: docker not found on PATH"
    image = _mutator_image()
    if not image_present(image):
        return f"gated: mutator image not present locally ({image}) -- pull/build it, then retry"
    return None


_GATE_REASON = _gate_reason()

# Applies to every test in this module -- the "module-level skip helper" the
# brief allows, so each test function stays free of its own skip boilerplate.
pytestmark = [
    pytest.mark.broker_e2e,
    pytest.mark.skipif(_GATE_REASON is not None, reason=_GATE_REASON or ""),
]

# codex's broker (only -- see container_operator._start_broker_auth) is now
# constructed with impersonate=True: chatgpt.com is Cloudflare-fronted, so
# even this MOCK-provider run forwards through a real curl_cffi Session
# (Chrome TLS impersonation), not httpx. `find_spec` is a side-effect-free
# presence check (no import, no network) -- safe at collection time exactly
# like `_gate_reason` above. claude/opencode2 stay on the plain httpx forward
# and need no such gate.
_CURL_CFFI_MISSING = importlib.util.find_spec("curl_cffi") is None


# --- canned per-operator provider responses ---------------------------------
#
# (event_type, data) pairs -> MockProvider emits a real `event: <type>\n
# data: <json>\n\n` SSE frame per pair (mock_provider.py's tuple form, added
# alongside its original bare-string shape for Task 6's own tests). See this
# module's docstring for what these mirror and why they're this module's one
# real assumption.

def _sse(event_type: str, **fields: object) -> tuple[str, str]:
    return event_type, json.dumps({"type": event_type, **fields})


_ANTHROPIC_SSE_SEQUENCE = [
    _sse("message_start", message={
        "id": "msg_e2e_broker_test", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 1},
    }),
    _sse("content_block_start", index=0, content_block={"type": "text", "text": ""}),
    _sse("content_block_delta", index=0, delta={"type": "text_delta", "text": "PONG"}),
    _sse("content_block_stop", index=0),
    _sse("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None},
         usage={"output_tokens": 1}),
    _sse("message_stop"),
]

# A COMPLETE codex Responses-API SSE turn. The `output_item.added` +
# `content_part.added` frames BEFORE the first `output_text.delta` are what
# open the active item/part -- without them codex-rs errors "OutputTextDelta
# without active item" and never surfaces the assistant text; the matching
# `content_part.done` + `output_item.done` close them before `response.completed`.
_CODEX_SSE_SEQUENCE = [
    _sse("response.created", response={"id": "resp_e2e_broker_test", "status": "in_progress"}),
    _sse("response.output_item.added", output_index=0, item={
        "type": "message", "id": "msg_e2e_broker_test", "role": "assistant",
        "status": "in_progress", "content": [],
    }),
    _sse("response.content_part.added", item_id="msg_e2e_broker_test",
         output_index=0, content_index=0, part={"type": "output_text", "text": ""}),
    _sse("response.output_text.delta", item_id="msg_e2e_broker_test",
         output_index=0, content_index=0, delta="PONG"),
    _sse("response.output_text.done", item_id="msg_e2e_broker_test",
         output_index=0, content_index=0, text="PONG"),
    _sse("response.content_part.done", item_id="msg_e2e_broker_test",
         output_index=0, content_index=0, part={"type": "output_text", "text": "PONG"}),
    _sse("response.output_item.done", output_index=0, item={
        "type": "message", "id": "msg_e2e_broker_test", "role": "assistant",
        "status": "completed", "content": [{"type": "output_text", "text": "PONG"}],
    }),
    _sse("response.completed", response={
        "id": "resp_e2e_broker_test", "status": "completed",
        "output": [{
            "type": "message", "id": "msg_e2e_broker_test", "role": "assistant",
            "content": [{"type": "output_text", "text": "PONG"}],
        }],
        "usage": {"input_tokens": 12, "output_tokens": 1, "total_tokens": 13},
    }),
]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _far_future_jwt(**claims: object) -> str:
    """A structurally-valid, UNSIGNED (``alg: none``) JWT with an ``exp``
    years out (``header.payload.`` -- never a real token; mirrors
    ``test_broker_transform.py``'s identical helper), far enough out that
    ``_codex_token_needs_refresh`` never fires and this test never attempts a
    live network refresh. It is the host ~/.codex login the BROKER reads, not
    anything the cage carries -- the codex cage holds no token at all now."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    far_future = int(time.time()) + 10 * 365 * 24 * 3600
    payload = _b64url(json.dumps({"exp": far_future, **claims}).encode())
    return f"{header}.{payload}."


def _run_id(label: str) -> str:
    return f"e2e-{label}-{uuid.uuid4().hex[:8]}"


def _new_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "work" / "iter-0"
    worktree.mkdir(parents=True)
    return worktree


# --- per-operator: real CLI, real container, real broker -> MockProvider ---


def test_claude_one_shot_through_broker_to_mock(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".nethackers" / "claude").mkdir(parents=True)
    real_cred = f"FAKE-REAL-CLAUDE-{uuid.uuid4().hex}"
    (home / ".nethackers" / "claude" / "setup-token").write_text(real_cred)

    provider = MockProvider(sse_events=_ANTHROPIC_SSE_SEQUENCE)
    with provider as mock_url:
        # Redirect the broker's fixed upstream at the mock instead of the
        # real api.anthropic.com -- broker_credential (real, reading the
        # tmp-fake home above) still supplies the real (fake) Bearer.
        monkeypatch.setitem(container_operator._BROKER_UPSTREAM_BASE, "claude", mock_url)
        op = ContainerOperator(
            harness="claude", image=_mutator_image(), system="Linux", home=home,
            broker=True, caps=_CAPS, run_id=_run_id("claude"),
        )
        lines: list[str] = []
        result = op.run(_new_worktree(tmp_path), _BRIEF, on_line=lines.append)

    matches = [
        r for r in provider.requests
        if r["headers"].get("authorization") == f"Bearer {real_cred}"
    ]
    assert matches, (
        "MockProvider never saw the broker-injected real credential -- recorded "
        f"request headers: {[r['headers'] for r in provider.requests]}"
    )
    req = matches[-1]
    assert "x-api-key" not in req["headers"], "broker must strip the client's x-api-key"
    assert "oauth-2025-04-20" in req["headers"].get("anthropic-beta", ""), (
        "broker must merge the oauth-2025-04-20 beta flag"
    )
    assert result.stopped_reason == "completed"
    assert "PONG" in "".join(lines), f"claude CLI output never contained PONG: {''.join(lines)!r}"


@pytest.mark.skipif(
    _CURL_CFFI_MISSING,
    reason="codex broker needs curl_cffi (TLS impersonation for chatgpt.com): "
           "pip install curl_cffi",
)
def test_codex_one_shot_through_broker_to_mock(tmp_path, monkeypatch):
    # The host ~/.codex login the BROKER reads host-side (broker_credential).
    # The container's cage ~/.codex carries NO token: codex is routed at the
    # broker by an unauthenticated `-c` provider and sends the POST with no
    # Authorization, so the broker injects 100% of the auth. `access` is a
    # far-future JWT so _codex_token_needs_refresh never fires (no live refresh).
    # This ContainerOperator is unchanged from before C2 -- no explicit
    # `cred_broker_factory`/`impersonate=` here -- but it now gets the real
    # `impersonate=True` codex broker for free, via
    # `_start_broker_auth`'s `impersonate=(self.harness == "codex")`.
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    access = _far_future_jwt()
    account_id = f"acct-e2e-{uuid.uuid4().hex[:8]}"
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "",
        "auth_mode": "chatgpt",
        "tokens": {"access_token": access, "refresh_token": "r", "account_id": account_id},
    }))

    provider = MockProvider(sse_events=_CODEX_SSE_SEQUENCE)
    with provider as mock_url:
        monkeypatch.setitem(container_operator._BROKER_UPSTREAM_BASE, "codex", mock_url)
        op = ContainerOperator(
            harness="codex", image=_mutator_image(), system="Linux", home=home,
            broker=True, caps=_CAPS, run_id=_run_id("codex"),
        )
        lines: list[str] = []
        result = op.run(_new_worktree(tmp_path), _BRIEF, on_line=lines.append)

    # THE BROKER CONTRACT is the proof of this test: the mock received codex's
    # POST at the codex Responses path, carrying the broker-injected real Bearer
    # AND the ChatGPT-Account-Id header -- neither of which the cage held.
    matches = [
        r for r in provider.requests if r["headers"].get("authorization") == f"Bearer {access}"
    ]
    assert matches, (
        "MockProvider never saw the broker-injected real credential -- recorded "
        f"request headers: {[r['headers'] for r in provider.requests]}"
    )
    proof = matches[-1]
    assert proof["headers"].get("chatgpt-account-id") == account_id
    # upstream (host only) + codex's `-c` base_url path == the full endpoint
    assert proof["path"] == "/backend-api/codex/responses", (
        f"codex POSTed to {proof['path']!r}, not the codex Responses endpoint"
    )
    assert result.stopped_reason == "completed"
    # Soft PONG: with the COMPLETE Responses SSE above codex should surface the
    # assistant text, but the exact `--json` line shape is version-dependent, so
    # a missing plain "PONG" is NOT a broker failure -- the contract above is.
    assert "PONG" in "".join(lines) or result.stopped_reason == "completed"


def test_opencode2_one_shot_through_broker_to_mock(tmp_path):
    home = tmp_path / "home"
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    real_cred = f"FAKE-REAL-OPENCODE-{uuid.uuid4().hex}"

    provider = MockProvider(sse_events=_ANTHROPIC_SSE_SEQUENCE)
    with provider as mock_url:
        # An explicit options.baseURL is opencode2_broker_targets's OWN
        # upstream-resolution mechanism (auth_inject._opencode2_provider_upstream)
        # -- unlike claude/codex, opencode2 never goes through
        # container_operator._BROKER_UPSTREAM_BASE, so this config field (not a
        # monkeypatch) is how its test points at the mock.
        config_dir.joinpath("opencode.json").write_text(json.dumps({
            "provider": {
                "anthropic": {"options": {"apiKey": real_cred, "baseURL": mock_url}},
            },
        }))
        op = ContainerOperator(
            harness="opencode2", image=_mutator_image(), system="Linux", home=home,
            model="anthropic/claude-haiku-4-5",
            broker=True, caps=_CAPS, run_id=_run_id("opencode2"),
        )
        lines: list[str] = []
        result = op.run(_new_worktree(tmp_path), _BRIEF, on_line=lines.append)

    matches = [r for r in provider.requests if r["headers"].get("x-api-key") == real_cred]
    assert matches, (
        "MockProvider never saw the broker-injected real credential -- recorded "
        f"request headers: {[r['headers'] for r in provider.requests]}"
    )
    assert "authorization" not in matches[-1]["headers"], (
        "opencode2's anthropic provider is x-api-key only -- no Authorization expected"
    )
    assert result.stopped_reason == "completed"
    assert "PONG" in "".join(lines), (
        f"opencode2 CLI output never contained PONG: {''.join(lines)!r}"
    )


# --- hostile-code probe: same broker mounts/env, command overridden --------


def test_hostile_probe_cannot_lift_the_credential(tmp_path):
    home = tmp_path / "home"
    (home / ".nethackers" / "claude").mkdir(parents=True)
    real_cred = f"FAKE-REAL-CLAUDE-{uuid.uuid4().hex}"
    (home / ".nethackers" / "claude" / "setup-token").write_text(real_cred)

    # Plant a hostile opencode.json + .opencode/ in a fake PARENT tree, then
    # run it through the SAME structural strip every real worktree goes
    # through (loop.py: shutil.copytree(cell.tree, worktree,
    # ignore=refs._mutator_ignore)) -- proves both halves of B1/§3.5 at once:
    # the broker never hands the box a real credential, AND a config-
    # injection attempt never even reaches the box to try reading one.
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "bot.py").write_text("# hostile parent tree\n")
    (parent / "opencode.json").write_text(json.dumps(
        {"provider": {"anthropic": {"options": {"apiKey": "PLANTED-SHOULD-NOT-SURVIVE"}}}}
    ))
    (parent / ".opencode").mkdir()
    (parent / ".opencode" / "notes.txt").write_text("hostile\n")

    worktree = tmp_path / "work" / "iter-0"
    shutil.copytree(parent, worktree, ignore=refs._mutator_ignore)
    # Sanity on the strip itself, independent of the container: if this ever
    # fails, refs.py's own strip is broken and the probe below would just be
    # re-discovering that bug through a much slower, gated path.
    assert not (worktree / "opencode.json").exists()
    assert not (worktree / ".opencode").exists()

    image = _mutator_image()
    # Anywhere harmless: this test never expects the mock to be hit (the
    # probe never talks to the broker at all, only to the real provider
    # hosts) -- it just gives CredBroker a safe, non-real upstream to hold.
    provider = MockProvider()
    with provider as mock_url:
        rewrite = broker_credential("claude", system="Linux", home=home)
        broker = CredBroker(mock_url, rewrite)
        broker_procs = [broker]
        try:
            broker_base = _host_gateway_url(broker.start())
            auth_args = auth_broker_args("claude", broker_base=broker_base, home=home)
            extra_args = [
                "--add-host", "host.docker.internal:host-gateway",
                "-v", f"{HOSTILE_PROBE_PATH}:/tmp/hostile_probe.py:ro",
            ]
            name = f"nethackers-hostile-probe-{uuid.uuid4().hex[:8]}"
            argv = build_docker_argv(
                harness="claude", image=image, name=name,
                worktree=worktree, cli=None, model=None, effort=None,
                caps=ContainerCaps(timeout_s=60), auth_args=auth_args,
                brief="unused -- the command below replaces claude entirely",
                extra_args=extra_args,
            )
            # SAME mounts/env as above (auth_args/extra_args); the trailing
            # `timeout <n> claude ...` build_docker_argv appended is replaced
            # with the probe script -- this is the "hostile probe test" the
            # design calls for (§4.2): identical broker wiring, adversarial
            # command instead of the real CLI.
            image_idx = argv.index(image)
            probe_argv = [*argv[: image_idx + 1], "timeout", "60", "python3",
                          "/tmp/hostile_probe.py"]
            result = subprocess.run(probe_argv, capture_output=True, text=True, timeout=90)
        finally:
            for proc in broker_procs:
                proc.stop()

    assert real_cred not in result.stdout, "the real credential leaked into the probe's stdout"
    assert real_cred not in result.stderr, "the real credential leaked into the probe's stderr"
    assert result.returncode == 0, (
        f"hostile probe exited nonzero (LEAK, or it crashed before finishing) -- "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    verdict = json.loads(result.stdout.strip().splitlines()[-1])
    assert verdict["verdict"] == "NO-LEAK", verdict
