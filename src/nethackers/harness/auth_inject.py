"""Host-login -> container auth injection (mutator-sandbox spec §3.9):
builds the ``docker run`` ``-v``/``-e`` args that hand a coding-agent CLI's
*existing* host login into an otherwise-fresh, ``--rm`` container -- so the
sandbox is authenticated with whatever account the user already logged into
on this machine. No new accounts, no API keys, no ``setup-token`` chore.

**Codex** bind-mounts the host's canonical ``~/.codex`` (rw) -- the *same*
file the host CLI already refreshes, never a copy. OpenAI's OAuth refresh
tokens are single-use/rotating, so sharing one canonical file is what keeps
the serial mutator loop's refreshes from ever colliding with a stale copy
(see ``docs/superpowers/research/2026-08-20-account-auth-in-sandbox.md``).

**Claude Code** stores its credential differently per host OS:

- Linux: a plain ``~/.claude/.credentials.json`` file -- mount it read-only
  and point ``CLAUDE_CONFIG_DIR`` at the mounted ``.claude`` dir.
- macOS: the credential lives in the Keychain, which a Linux container can
  never reach, so this module reads it host-side (``security
  find-generic-password``) and hands the container only the resulting OAuth
  access token via ``CLAUDE_CODE_OAUTH_TOKEN``. That env var is
  container-only -- setting it in the host's own Mac shell can silently
  delete the Keychain entry on exit (claude-code#37512).

Only the credential itself ever crosses the host/container boundary, never
the harness's memory/session/history, so this doesn't reopen the
hermeticity gap the operator's flags (``harness/operator.py``) already
close.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_CLAUDE_LOGIN_HINT = "run `claude` on this host to log in, then retry"


class AuthUnavailable(Exception):
    """Raised when ``harness`` has no resolvable login on this host -- no
    credential file/dir where expected, or a Keychain miss on macOS.
    Carries ``harness`` and a ready-to-print ``hint`` naming the login
    command that fixes it.
    """

    def __init__(self, harness: str, hint: str):
        self.harness = harness
        self.hint = hint
        super().__init__(f"no usable {harness} login on this host -- {hint}")


def auth_docker_args(
    harness: str,
    *,
    system: str,
    run=subprocess.run,
    home: Path,
    _require_exists: bool = False,
) -> list[str]:
    """Build the ``-v``/``-e`` args that inject ``harness``'s existing host
    login into the container (to be spliced into ``docker run`` argv
    alongside the workspace mount -- see ``container_operator.py``).

    ``run`` is the ``subprocess.run``-shaped callable used for the macOS
    Keychain read (injectable for tests). ``_require_exists`` is off by
    default: the codex/claude-Linux branches are otherwise pure string
    formatting, which is what lets their tests pass a fake, non-existent
    ``home`` without touching the real filesystem; a caller that opts in
    gets an early, friendly ``AuthUnavailable`` instead of silently mounting
    a path Docker would otherwise just create empty.
    """
    if harness == "codex":
        if _require_exists and not (home / ".codex").exists():
            raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
        return ["-v", f"{home}/.codex:/home/agent/.codex"]

    if harness == "claude":
        if system == "Darwin":
            return _claude_macos_env(run)
        if _require_exists and not (home / ".claude" / ".credentials.json").exists():
            raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
        return [
            "-v",
            f"{home}/.claude/.credentials.json:/home/agent/.claude/.credentials.json:ro",
            "-e", "CLAUDE_CONFIG_DIR=/home/agent/.claude",
        ]

    raise ValueError(f"unknown harness: {harness!r}")


def _claude_macos_env(run) -> list[str]:
    """Read the Claude Code Keychain item host-side and return the
    container-only ``CLAUDE_CODE_OAUTH_TOKEN`` env arg. Never touches the
    host shell/file -- the token only ever flows into the container's ``-e``.
    """
    result = run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    try:
        token = json.loads(result.stdout)["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT) from exc
    return ["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"]
