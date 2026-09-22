"""Gated LIVE smoke per operator (Task 8, design §4.2's last bullet / §7):
the REAL agent CLI (claude / codex / opencode2), in its REAL mutator
container, run ONE-SHOT through a REAL ``cred_broker.CredBroker`` into the
REAL provider (api.anthropic.com / chatgpt.com's backend / the configured
OpenCode provider) -- no mock, no fake login anywhere. This is the live
counterpart to Task 7's ``test_broker_e2e.py``, which it mirrors
structurally (the same ``_gate_reason``-shaped skip helper, the same
one-shot "reply with exactly PONG" brief, the same
``ContainerOperator``/``ContainerCaps`` one-shot invocation) but differs in
the two ways that matter:

- **No ``MockProvider``, no monkeypatched upstream.**
  ``container_operator._BROKER_UPSTREAM_BASE`` is used AS-IS (the real
  ``api.anthropic.com`` / ``chatgpt.com/backend-api/codex``); OpenCode's
  upstream comes from whatever ``opencode2_broker_targets`` resolves out of
  the REAL global config. The broker really talks to the real provider.
- **No ``tmp_path`` fake login.** ``home=Path.home()`` (this host's real
  home), so ``broker_credential``/``auth_broker_args`` read the REAL Claude
  setup-token/keychain, the REAL ``~/.codex/auth.json``, or the REAL global
  ``~/.config/opencode/opencode.json[c]`` -- exactly what a live evolve run
  would read. The broker then injects a GENUINE credential into the request
  it forwards; this module's own code never touches the credential value
  itself (it flows entirely inside ``broker_credential``/``CredBroker``,
  called only indirectly through ``ContainerOperator(broker=True, ...)``)
  -- nothing here logs, prints, or asserts on it.

This costs real provider tokens and needs a real interactive login already
on this host, so it is gated far more tightly than Task 7's mock tier: set
``NETHACKERS_E2E_LIVE=1`` (deliberately a DIFFERENT flag from Task 7's
``NETHACKERS_E2E`` -- so a bare ``NETHACKERS_E2E=1 uv run pytest
tests/e2e/`` sweep of the whole directory can never silently spend real
tokens as a side effect of Task 7's own habit) in addition to Docker + the
mutator image + that operator's real login being resolvable on THIS host.
Missing any one of those is a clean, explicit skip naming what's missing --
this module NEVER runs the real CLI against a missing/partial login (a
Keychain miss, a missing ``~/.codex``, an empty/keyless global OpenCode
config all just skip; see ``_claude_gate_reason`` / ``_codex_gate_reason`` /
``_opencode_gate_reason``).

Run one operator's live smoke by hand once you're logged in for it:

    NETHACKERS_E2E_LIVE=1 uv run python -m pytest tests/e2e/test_broker_live.py -m claude_live -q
    NETHACKERS_E2E_LIVE=1 uv run python -m pytest tests/e2e/test_broker_live.py -m codex_live -q
    NETHACKERS_E2E_LIVE=1 uv run python -m pytest tests/e2e/test_broker_live.py -m opencode_live -q

or all three at once (whichever lack a resolvable login on this host still
skip individually) -- see ``make broker-live``.

**Codex is the one that resolves this design's three live-gated questions**
(``auth_inject.py``'s module docstring, "Live-gated"): whether the
subscription codex actually honours the cage ``config.toml``'s
``openai_base_url`` in ``chatgpt`` mode; whether it accepts the placeholder
far-``exp`` JWT without attempting its own refresh; and how it derives
``ChatGPT-Account-Id``. The cage mounts ONLY a placeholder JWT + the real
account-id (never a real token, B1) -- so if codex ever ignored the broker
and hit the real ``chatgpt.com`` backend directly with that placeholder,
OpenAI's real backend would reject it outright. **A codex 403 / Cloudflare
block / an auth-refresh error in this test is therefore exactly that
signal** -- per spec §7 the correct response is to stage codex back onto
the self-refreshing credential MOUNT (``auth_docker_args``,
``broker=False``), NOT to weaken the broker (never relax B1 by mounting a
real token into the cage, and never widen what the cage's placeholder is
allowed to be). See ``_CODEX_LIVE_ADVISORY`` below, which every codex
assertion failure repeats verbatim.

ASSUMPTIONS/residuals this module can't resolve without the gated run
itself (this module has never been run against real logins -- that needs
the gated Docker/image/login state this authoring environment doesn't
have):

- **Which OpenCode provider is live-tested** depends entirely on this
  host's real global config: ``_opencode_live_target`` prefers whichever of
  ``anthropic``/``openai`` ``opencode2_broker_targets`` resolves (mirroring
  Task 6/7's own ``anthropic/claude-3-5-haiku-20241022`` choice), or
  ``NETHACKERS_OPENCODE_LIVE_MODEL`` (``"<provider>/<model-id>"``) when set;
  a config with only an unrecognized custom provider name skips with a
  clear reason rather than guessing a model id blind.
- **Real-model wording.** The brief asks for exactly ``PONG``; a real model
  usually complies with such an explicit, trivial instruction, but isn't
  contractually guaranteed to -- so "completed but no literal PONG" is
  asserted (and reported) SEPARATELY from "never completed at all": the
  former means the broker round-trip genuinely worked and only the exact
  wording differs; the latter means the broker/login/upstream path itself
  is broken.
"""
from __future__ import annotations

import os
import platform
import shutil
import uuid
from pathlib import Path

import pytest

from nethackers.config import load_stage
from nethackers.harness.auth_inject import (
    AuthUnavailable,
    _codex_creds,
    auth_docker_args,
    opencode2_broker_targets,
)
from nethackers.harness.container_operator import ContainerCaps, ContainerOperator
from nethackers.harness.sandbox_preflight import image_present, resolve_image

_BRIEF = "Reply with exactly the single word PONG and nothing else."

# A one-shot "reply with one word" smoke doesn't need production's 8h
# runaway ceiling (ContainerCaps.timeout_s's default). Wider than Task 7's
# own 180s _CAPS: that one talks to an instant local MockProvider, this one
# to a real provider over the real network.
_CAPS = ContainerCaps(timeout_s=300)


def _mutator_image() -> str:
    # resolve_image has NO side effects (its own docstring) -- safe to call
    # at collection time and again per-test.
    return resolve_image(load_stage().mutator_image, "mutator")


def _run_id(label: str) -> str:
    return f"live-{label}-{uuid.uuid4().hex[:8]}"


def _new_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "work" / "iter-0"
    worktree.mkdir(parents=True)
    return worktree


def _common_gate_reason() -> str | None:
    """The prefix every per-operator gate below layers its own real-login
    check onto: Docker + the mutator image, needed regardless of which
    operator. Short-circuits on the env var FIRST (mirrors
    ``test_broker_e2e.py``'s ``_gate_reason``) so collection with
    ``NETHACKERS_E2E_LIVE`` unset -- this module's default, every-PR state
    -- never touches Docker, the Keychain, ``~/.codex``, or the opencode
    config."""
    if os.environ.get("NETHACKERS_E2E_LIVE") != "1":
        return ("gated: set NETHACKERS_E2E_LIVE=1 to run the LIVE per-operator broker "
                 "smoke (spends real provider tokens; needs Docker + the mutator image "
                 "+ a real host login for this operator)")
    if shutil.which("docker") is None:
        return "gated: docker not found on PATH"
    image = _mutator_image()
    if not image_present(image):
        return f"gated: mutator image not present locally ({image}) -- pull/build it, then retry"
    return None


# Computed ONCE at import time (not per-operator): with NETHACKERS_E2E_LIVE
# unset, _common_gate_reason short-circuits before touching Docker at all,
# so this stays cheap; once set, this avoids three redundant `docker image
# inspect` subprocess calls (one per operator) for the exact same answer.
_COMMON_GATE_REASON = _common_gate_reason()


def _claude_gate_reason() -> str | None:
    if _COMMON_GATE_REASON is not None:
        return _COMMON_GATE_REASON
    try:
        # The exact existence check auth_docker_args itself performs for the
        # mount path (_require_exists=True) -- on Darwin this also reads the
        # Keychain (the same read broker_credential makes at run time). The
        # return value (the real token, on Darwin) is deliberately discarded:
        # this call is used only to see whether it RAISES.
        auth_docker_args(
            "claude", system=platform.system(), home=Path.home(), _require_exists=True,
        )
    except AuthUnavailable as exc:
        return f"gated: no real claude login on this host -- {exc.hint}"
    return None


def _codex_gate_reason() -> str | None:
    if _COMMON_GATE_REASON is not None:
        return _COMMON_GATE_REASON
    try:
        _codex_creds(Path.home())  # parses ~/.codex/auth.json; return value discarded
    except AuthUnavailable as exc:
        return f"gated: no real codex login on this host -- {exc.hint}"
    return None


# provider name -> a cheap, known-current model id for the live smoke's
# --model, when the real global OpenCode config brokers that provider but
# this module has no other way to pin one. Mirrors test_broker_e2e.py's own
# "anthropic/claude-3-5-haiku-20241022" choice; NETHACKERS_OPENCODE_LIVE_MODEL
# overrides this outright for any other brokered provider name.
_OPENCODE_LIVE_MODEL_BY_PROVIDER = {
    "anthropic": "claude-3-5-haiku-20241022",
    "openai": "gpt-4o-mini",
}


def _opencode_live_target(home: Path) -> tuple[str | None, str | None]:
    """``(model, reason)`` -- ``model`` is ``"<provider>/<model-id>"`` for
    the first real brokerable provider ``opencode2_broker_targets`` resolves
    out of this host's global config that either
    ``NETHACKERS_OPENCODE_LIVE_MODEL`` or ``_OPENCODE_LIVE_MODEL_BY_PROVIDER``
    names a model for; ``reason`` is the skip message when none qualifies.
    Never touches the resolved provider's real key value -- only
    ``target["name"]`` (never ``target["rewrite"]``) is read here."""
    targets = opencode2_broker_targets(home=home, environ=os.environ)
    if not targets:
        return None, (
            "gated: no brokerable OpenCode provider in the global config "
            "(~/.config/opencode/opencode.json[c]) -- configure a real apiKey for a "
            "provider named anthropic/openai (or set NETHACKERS_OPENCODE_LIVE_MODEL), "
            "then retry"
        )
    override = os.environ.get("NETHACKERS_OPENCODE_LIVE_MODEL")
    if override:
        return override, None
    for target in targets:
        model_id = _OPENCODE_LIVE_MODEL_BY_PROVIDER.get(target["name"])
        if model_id:
            return f"{target['name']}/{model_id}", None
    names = ", ".join(sorted({t["name"] for t in targets}))
    return None, (
        f"gated: brokerable OpenCode provider(s) found ({names}) but none has a known "
        "default model id for the live smoke -- set NETHACKERS_OPENCODE_LIVE_MODEL="
        "'<provider>/<model-id>', then retry"
    )


def _opencode_gate_reason() -> str | None:
    if _COMMON_GATE_REASON is not None:
        return _COMMON_GATE_REASON
    _, target_reason = _opencode_live_target(Path.home())
    return target_reason


_CLAUDE_GATE_REASON = _claude_gate_reason()
_CODEX_GATE_REASON = _codex_gate_reason()
_OPENCODE_GATE_REASON = _opencode_gate_reason()


_CODEX_LIVE_ADVISORY = (
    "If this looks like a 403 / a Cloudflare block / an auth-refresh error, that IS "
    "the spec §7 signal to fall back to staging codex onto the self-refreshing "
    "credential MOUNT (auth_docker_args, broker=False) -- not a reason to weaken the "
    "broker (never relax B1 by mounting a real token into the cage)."
)
_CLAUDE_LIVE_ADVISORY = (
    "A failure here means the broker's real round-trip to api.anthropic.com broke -- "
    "confirm the setup-token / keychain login is still valid before suspecting the "
    "broker itself regressed."
)
_OPENCODE_LIVE_ADVISORY = (
    "A failure here means the broker's real round-trip to the configured OpenCode "
    "provider broke -- confirm the global opencode.json[c] provider key is still valid."
)


def _live_round_trip(op: ContainerOperator, worktree: Path, *, advisory: str) -> str:
    """Run ``_BRIEF`` through ``op`` and return the accumulated CLI output.

    Any failure -- a non-zero exit (``run_operator`` raises ``RuntimeError``
    carrying the useful output tail) or anything else -- becomes a
    ``pytest.fail`` carrying the FULL accumulated output plus ``advisory``
    (what a failure here means for this operator), rather than a bare
    traceback: the whole point of this gated tier is a human reading exactly
    what the real provider/CLI said back.
    """
    lines: list[str] = []
    try:
        result = op.run(worktree, _BRIEF, on_line=lines.append)
    except Exception as exc:
        pytest.fail(
            f"{op.harness} did not complete cleanly through the LIVE broker: {exc}\n"
            f"accumulated output: {''.join(lines)!r}\n{advisory}"
        )
    output = "".join(lines)
    assert result.stopped_reason == "completed", (
        f"{op.harness} stopped_reason={result.stopped_reason!r} (expected 'completed') "
        f"-- output: {output!r}\n{advisory}"
    )
    return output


# --- per-operator: real CLI, real container, real broker -> REAL provider --


@pytest.mark.claude_live
@pytest.mark.skipif(_CLAUDE_GATE_REASON is not None, reason=_CLAUDE_GATE_REASON or "")
def test_claude_live_round_trip_through_broker(tmp_path):
    op = ContainerOperator(
        harness="claude", image=_mutator_image(), system=platform.system(),
        home=Path.home(), broker=True, caps=_CAPS, run_id=_run_id("claude"),
    )
    output = _live_round_trip(op, _new_worktree(tmp_path), advisory=_CLAUDE_LIVE_ADVISORY)
    assert "PONG" in output, (
        "claude completed through the LIVE broker but never said PONG -- a real "
        f"completion came back, just not verbatim: {output!r}"
    )


@pytest.mark.codex_live
@pytest.mark.skipif(_CODEX_GATE_REASON is not None, reason=_CODEX_GATE_REASON or "")
def test_codex_live_round_trip_through_broker(tmp_path):
    op = ContainerOperator(
        harness="codex", image=_mutator_image(), system=platform.system(),
        home=Path.home(), broker=True, caps=_CAPS, run_id=_run_id("codex"),
    )
    output = _live_round_trip(op, _new_worktree(tmp_path), advisory=_CODEX_LIVE_ADVISORY)
    assert "PONG" in output, (
        f"codex completed through the LIVE broker but never said PONG: {output!r}\n"
        f"{_CODEX_LIVE_ADVISORY}"
    )


@pytest.mark.opencode_live
@pytest.mark.skipif(_OPENCODE_GATE_REASON is not None, reason=_OPENCODE_GATE_REASON or "")
def test_opencode_live_round_trip_through_broker(tmp_path):
    model, _ = _opencode_live_target(Path.home())
    op = ContainerOperator(
        harness="opencode2", image=_mutator_image(), system=platform.system(),
        home=Path.home(), model=model, broker=True, caps=_CAPS, run_id=_run_id("opencode2"),
    )
    output = _live_round_trip(op, _new_worktree(tmp_path), advisory=_OPENCODE_LIVE_ADVISORY)
    assert "PONG" in output, (
        f"opencode2 completed through the LIVE broker but never said PONG: {output!r}"
    )
