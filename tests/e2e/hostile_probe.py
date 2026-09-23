#!/usr/bin/env python3
"""Hostile-code probe (Task 7, design §4.2): a STANDALONE, stdlib-only script
that runs INSIDE the mutator container -- as the container's whole command,
in place of the real coding-agent CLI -- with the SAME broker mounts/env a
real broker-mediated run gets (see ``test_broker_e2e.py``'s hostile-probe
test). It plays the part of an adversarial "mutation" trying every way to
lift the real model-provider credential back out of the sandbox (B1, design
§2/§4.2):

  1. every env var this codebase's broker/mount paths are documented to ever
     put a credential in (``auth_inject.py``) -- must be the PLACEHOLDER
     shape only, never a real token. Scoped to exactly those names on
     purpose: a generic "anything TOKEN/SECRET-shaped" scan would both
     false-positive on unrelated ambient secrets this process's own
     environment may carry, and miss the real gap (codex's credential lives
     in a FILE, never an env var -- see #2).
  2. any mounted credential FILE (the exact paths ``auth_inject.py`` mounts:
     claude's ``.credentials.json``, codex's cage ``auth.json``, opencode's
     cage ``opencode.json``/``.jsonc``) -- read whatever is there and check
     the same placeholder shape.
  3. a DIRECT outbound call to each real provider host, bypassing the broker
     entirely, using whatever credential-shaped material steps 1-2 found (or
     an inert placeholder if they found nothing) -- must not come back with
     a real (2xx) response. A network failure or a 4xx is the EXPECTED,
     passing outcome here: the point is that nothing reachable from inside
     the box can complete a real authenticated call, not that the box has no
     network at all.
  4. whether a planted ``opencode.json``/``opencode.jsonc``/``.opencode``
     survived the structural strip (``refs.py``'s ``_AGENT_CONFIG_NAMES``,
     design §3.5) into ``/workspace`` -- an attacker that got such a file
     INTO the worktree would want OpenCode to read it and leak a provider
     key back out; the file must not even be present for that.

Prints exactly one line of JSON to stdout: ``{"verdict": "LEAK"|"NO-LEAK",
...findings...}``. Exit code 0 for NO-LEAK, 1 for LEAK -- a caller checks
BOTH the exit code and the printed verdict, never just one.

Stdlib only (``urllib``, not ``httpx``) -- this runs as whatever ``python3``
the mutator image happens to ship, not the host's own venv, and per the
design's own instruction, the untrusted-facing side of a sandbox boundary
carries no extra dependencies to trust.

Safe to run standalone, outside any container (e.g. `python3
hostile_probe.py` on a bare dev host, for this file's own collection-time
sanity check): every path below degrades to "absent"/"unreachable" rather
than raising, and the direct-call step only ever uses a curated, known-name
allowlist -- never a broad scan of this process's ambient environment -- so
it cannot pick up and dispatch an unrelated real secret that happens to be
set in whatever shell runs it.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# The ONLY env vars this codebase's broker/mount paths ever put a
# claude/codex/opencode credential in (auth_inject.py: auth_broker_args /
# auth_docker_args / the opencode2 cage config's forwarded-by-name vars).
# Deliberately NOT a generic TOKEN/SECRET/KEY regex over all of os.environ --
# see the module docstring's point 1. (The header each would ride on --
# x-api-key for ANTHROPIC_API_KEY, Authorization: Bearer for the rest -- only
# matters for attempt_direct_calls below, via _DIRECT_CALL_TARGETS.)
_WATCHED_ENV_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN",   # claude broker placeholder / OAuth mount
    "ANTHROPIC_API_KEY",         # claude API-key mount / opencode-anthropic
    "ANTHROPIC_AUTH_TOKEN",      # some claude CLI versions use this name
    "OPENAI_API_KEY",            # codex API-key mount / opencode-openai
)

# The exact paths auth_inject.py mounts a credential into (Dockerfile.mutator:
# HOME=/home/agent). A path not existing is normal for a harness whose broker
# path uses no mount at all (e.g. claude: env-only, nothing here) -- absence
# is not a finding.
_MOUNTED_CRED_FILES = (
    Path("/home/agent/.claude/.credentials.json"),
    Path("/home/agent/.codex/auth.json"),
    Path("/home/agent/.config/opencode/opencode.json"),
    Path("/home/agent/.config/opencode/opencode.jsonc"),
)

# refs.py's _AGENT_CONFIG_NAMES subset relevant to a credential-exfiltration
# attempt (the OpenCode config-injection vector, design §3.5) -- checked
# directly under /workspace, where the worktree is bind-mounted.
_PLANTED_CONFIG_NAMES = ("opencode.json", "opencode.jsonc", ".opencode")

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_CHATGPT_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"

# An inert, obviously-fake fallback used when a target below found nothing to
# try -- so the direct-call check still exercises "can a call complete at
# all", not just "did we find something suspicious first". Never a value
# that could coincidentally be a real credential.
_NO_CREDENTIAL_MARKER = "no-credential-in-sandbox"


def _is_placeholder(value: str) -> bool:
    """Whether ``value`` matches a KNOWN non-secret placeholder shape: claude's
    literal ``"proxy-managed"``, or a structurally-valid but UNSIGNED JWT
    (``header.payload.`` -- ``alg: none``, deliberately empty signature
    segment). A genuine, signed JWT has a non-empty third segment. (The codex
    cage now carries NO token at all -- its auth is broker-injected on the wire
    -- so this is a defensive shape check, not a value the box is handed.)"""
    if value in ("proxy-managed", "PLACEHOLDER", _NO_CREDENTIAL_MARKER):
        return True
    parts = value.split(".")
    return len(parts) == 3 and bool(parts[0]) and bool(parts[1]) and parts[2] == ""


def check_env() -> list[dict]:
    findings = []
    for name in _WATCHED_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            findings.append({"source": f"env:{name}", "present": False})
            continue
        findings.append({
            "source": f"env:{name}", "present": True, "placeholder": _is_placeholder(value),
        })
    return findings


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        return None


def _extract_credential_fields(doc: object) -> list[tuple[str, str]]:
    """Best-effort walk of the handful of JSON shapes this codebase's own
    cred files actually use -- claude's ``.credentials.json``
    (``claudeAiOauth.accessToken``), codex's ``auth.json``
    (``tokens.access_token``), opencode's ``opencode.json``
    (``provider.<name>.options.apiKey``). Anything else in the file is not
    this probe's concern."""
    out: list[tuple[str, str]] = []
    if not isinstance(doc, dict):
        return out
    claude_oauth = doc.get("claudeAiOauth")
    if isinstance(claude_oauth, dict) and isinstance(claude_oauth.get("accessToken"), str):
        out.append(("claudeAiOauth.accessToken", claude_oauth["accessToken"]))
    tokens = doc.get("tokens")
    if isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str):
        out.append(("tokens.access_token", tokens["access_token"]))
    providers = doc.get("provider")
    if isinstance(providers, dict):
        for pname, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            options = provider.get("options")
            api_key = options.get("apiKey") if isinstance(options, dict) else None
            if isinstance(api_key, str):
                out.append((f"provider.{pname}.options.apiKey", api_key))
    return out


def check_files() -> list[dict]:
    findings = []
    for path in _MOUNTED_CRED_FILES:
        doc = _read_json(path)
        if doc is None:
            findings.append({"source": f"file:{path}", "present": False})
            continue
        fields = _extract_credential_fields(doc)
        if not fields:
            findings.append({"source": f"file:{path}", "present": True, "fields": []})
            continue
        for label, value in fields:
            findings.append({
                "source": f"file:{path}#{label}", "present": True,
                "placeholder": _is_placeholder(value) if value else True,
            })
    return findings


def check_planted_configs(workspace: Path = Path("/workspace")) -> list[dict]:
    return [
        {"source": f"planted:{workspace / name}", "survived": (workspace / name).exists()}
        for name in _PLANTED_CONFIG_NAMES
    ]


def _codex_file_candidate() -> str | None:
    """The one file-only credential candidate (codex has no env var at
    all) -- read straight from the same mounted path ``check_files``
    already inspected, kept separate so ``attempt_direct_calls`` doesn't
    need to search ``check_files``'s findings back apart."""
    doc = _read_json(Path("/home/agent/.codex/auth.json"))
    if not isinstance(doc, dict):
        return None
    tokens = doc.get("tokens")
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    return access if isinstance(access, str) and access else None


def _env_candidate(name: str) -> Callable[[], str | None]:
    return lambda: os.environ.get(name) or None


# One direct-call target per provider surface this codebase brokers for
# (design §3.2's table): (label, url, header_name, a zero-arg lookup for a
# real candidate value if any). The codex row has no env var at all (its
# credential is file-only, see _MOUNTED_CRED_FILES) -- its lookup reads the
# mounted file directly instead.
_DIRECT_CALL_TARGETS: tuple[tuple[str, str, str, Callable[[], str | None]], ...] = (
    ("anthropic", _ANTHROPIC_MESSAGES_URL, "x-api-key", _env_candidate("ANTHROPIC_API_KEY")),
    ("anthropic-oauth", _ANTHROPIC_MESSAGES_URL, "Authorization",
     _env_candidate("CLAUDE_CODE_OAUTH_TOKEN")),
    ("openai", _OPENAI_CHAT_URL, "Authorization", _env_candidate("OPENAI_API_KEY")),
    ("chatgpt-backend", _CHATGPT_BACKEND_URL, "Authorization", _codex_file_candidate),
)


def attempt_direct_calls() -> list[dict]:
    """Fire one real outbound call per provider surface (``_DIRECT_CALL_TARGETS``),
    bypassing the broker entirely, using whatever credential-shaped value is
    actually visible in the box for that provider (env for claude/openai, the
    mounted file for codex) -- or an inert marker when nothing is visible, so
    this still proves "a direct call can't complete" even in a maximally
    clean box. A connection failure or a non-2xx status is the EXPECTED,
    passing result; only a genuine 2xx counts as a leak."""
    results = []
    for label, url, header_name, candidate in _DIRECT_CALL_TARGETS:
        value = candidate() or _NO_CREDENTIAL_MARKER
        header_value = value if header_name == "x-api-key" else f"Bearer {value}"
        req = urllib.request.Request(
            url, data=b"{}", method="POST",
            headers={header_name: header_value, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                status = resp.getcode()
            results.append({"label": label, "url": url, "status": status,
                            "leaked": 200 <= status < 300})
        except urllib.error.HTTPError as exc:
            # A real (non-2xx) response from the real provider -- exactly
            # the expected outcome for a placeholder/inert credential.
            results.append({"label": label, "url": url, "status": exc.code, "leaked": False})
        except Exception as exc:  # noqa: BLE001 - any failure here means no real call completed
            results.append({"label": label, "url": url, "error": str(exc), "leaked": False})
    return results


def main() -> int:
    env_findings = check_env()
    file_findings = check_files()
    planted = check_planted_configs()
    call_results = attempt_direct_calls()

    non_placeholder_present = any(
        f.get("present") and f.get("placeholder") is False
        for f in (*env_findings, *file_findings)
    )
    any_call_leaked = any(r.get("leaked") for r in call_results)
    any_planted_survived = any(f["survived"] for f in planted)

    verdict = (
        "LEAK" if (non_placeholder_present or any_call_leaked or any_planted_survived)
        else "NO-LEAK"
    )
    report = {
        "verdict": verdict,
        "env": env_findings,
        "files": file_findings,
        "planted_configs": planted,
        "direct_calls": call_results,
    }
    print(json.dumps(report))
    return 0 if verdict == "NO-LEAK" else 1


if __name__ == "__main__":
    sys.exit(main())
