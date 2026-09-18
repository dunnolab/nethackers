"""Host-login -> container auth injection (mutator-sandbox spec §3.9):
builds the ``docker run`` ``-v``/``-e`` args that hand a coding-agent CLI's
*existing* host login into an otherwise-fresh, ``--rm`` container -- so the
sandbox is authenticated with whatever account the user already logged into
on this machine. No new accounts, no API keys, no ``setup-token`` chore.

**Codex** bind-mounts the host's canonical ``~/.codex`` (rw) -- the *same*
file the host CLI already refreshes, never a copy. OpenAI's OAuth refresh
tokens are single-use/rotating, so sharing one canonical file is what keeps
the serial mutator loop's refreshes from ever colliding with a stale copy.

**OpenCode 2** gets only the ``provider`` section of the host's global
``opencode.json`` / ``opencode.jsonc``, copied to an owner-only file under
``~/.nethackers/opencode2`` and mounted read-only, plus the environment
variables those providers reference (forwarded by name, never copied into
argv). Plugins, MCP servers and instructions stay on the host, and
``OPENCODE_DISABLE_PROJECT_CONFIG`` keeps OpenCode from reading the worktree's
own config. Logins made with ``opencode2 auth login`` live in OpenCode's
database, which the container never sees. The container keeps its own
sessions and database ephemeral.

**Claude Code** stores its credential differently per host OS:

- Linux: a plain ``~/.claude/.credentials.json`` file -- mount it read-only
  and point ``CLAUDE_CONFIG_DIR`` at the mounted ``.claude`` dir.
- macOS: the credential lives in the Keychain, which a Linux container can
  never reach, so this module reads it host-side (``security
  find-generic-password``) and hands the container only the resulting OAuth
  access token via ``CLAUDE_CODE_OAUTH_TOKEN``. That env var is
  container-only -- setting it in the host's own Mac shell can silently
  delete the Keychain entry on exit (claude-code#37512).

Only credentials, and for OpenCode 2 its provider definitions, cross the
host/container boundary; session databases and history remain outside the
sandbox.

**Broker path (opt-in, §3d/INV2).** ``auth_broker_args`` is a SELECTABLE
alternative to the mount functions above: instead of handing the container a
real credential, it hands it a placeholder key plus the harness's own
base-URL override, pointing it at ``cred_broker.CredBroker`` -- which holds
the real credential host-side and injects it only into requests it forwards
to the one provider host. ``broker_credential`` is the host-side read that
gets the broker its real ``header_value``, reusing this module's exact
login paths (macOS Keychain / ``.credentials.json`` for Claude, ``~/.codex``
for Codex) rather than duplicating them. ``ContainerOperator``'s ``broker``
flag chooses between the two; the mount stays the default -- see its
docstring for why.

Two things this pairing does NOT resolve, both live-verification concerns
(PARKED, not a correctness claim of this module):

- **Scheme mismatch for Claude.** The broker path's placeholder is
  API-key-shaped (``ANTHROPIC_API_KEY`` / ``x-api-key`` header, Anthropic's
  API-key convention -- see ``harness/discovery.py``'s own
  ``_claude_auth_headers``), but the credential ``broker_credential`` reads
  host-side is the Claude Code OAuth access token -- Bearer-shaped, and
  normally paired with an ``anthropic-beta`` header ``CredBroker`` has no way
  to also inject (it forwards exactly one header). Forwarding that token
  under ``x-api-key`` may not authenticate; a host with a real
  ``ANTHROPIC_API_KEY`` of its own would match cleanly and is the easy fix,
  but reading that env var host-side wasn't part of this task's ask, so this
  module doesn't guess at it.
- **Codex token staleness.** ``~/.codex/auth.json``'s OAuth ``access_token``
  (the fallback when there's no stable ``OPENAI_API_KEY`` login) rotates;
  ``broker_credential`` reads it once, at container start, and the broker
  keeps injecting that same snapshot for the container's whole (up to 8h)
  lifetime. The mount path sidesteps this entirely by sharing the live,
  self-refreshing file instead of a value copied out of it once.

OpenCode 2 has no broker form at all: its base-URL override is a
per-provider JSON config field (``options.baseURL``), not a single env var
this module could point at the broker independent of which (arbitrary)
provider is configured, and rewriting that safely is unverified -- so
``auth_broker_args`` raises rather than guessing, and callers keep using the
credential mount for it either way.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
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
    environ: Mapping[str, str] | None = None,
    _require_exists: bool = False,
) -> list[str]:
    """Build the ``-v``/``-e`` args that inject ``harness``'s existing host
    login into the container (to be spliced into ``docker run`` argv
    alongside the workspace mount -- see ``container_operator.py``).

    ``run`` is the ``subprocess.run``-shaped callable used for the macOS
    Keychain read (injectable for tests). ``_require_exists`` is off by
    default for the login-only backends; a caller that opts in gets an early,
    friendly ``AuthUnavailable`` instead of silently mounting a path Docker
    would otherwise create empty. OpenCode 2 never raises: without a provider
    key it still runs OpenCode's free models.
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

    if harness == "opencode2":
        return _opencode2_docker_args(home, environ=environ)

    raise ValueError(f"unknown harness: {harness!r}")


def auth_broker_args(harness: str, *, broker_base: str) -> list[str]:
    """The ``-e`` args that point ``harness`` at the credential broker
    (``cred_broker.CredBroker``, started at ``broker_base``) instead of
    mounting its host login: a PLACEHOLDER key plus the harness's own
    base-URL override. No ``-v`` mount, no real key -- see this module's
    docstring for the broker path's known live-verification concerns, and
    ``ContainerOperator``'s ``broker`` flag for how a caller opts into this
    instead of ``auth_docker_args``'s mount (the default).
    """
    if harness == "claude":
        return ["-e", f"ANTHROPIC_BASE_URL={broker_base}", "-e", "ANTHROPIC_API_KEY=proxy-managed"]

    if harness == "codex":
        return ["-e", f"OPENAI_BASE_URL={broker_base}", "-e", "OPENAI_API_KEY=proxy-managed"]

    if harness == "opencode2":
        # OpenCode's base-URL override is a per-provider JSON config field
        # (`options.baseURL`), not a single env var this function could point
        # at the broker independent of which (arbitrary) provider is
        # configured -- rewriting the cage config safely is unverified, so
        # this deliberately doesn't guess. Callers keep using the credential
        # mount (`auth_docker_args`) for opencode2.
        raise NotImplementedError(
            "broker unsupported for opencode2 -- use the credential mount "
            "(auth_docker_args) instead"
        )

    raise ValueError(f"unknown harness: {harness!r}")


def broker_credential(
    harness: str, *, system: str, home: Path, run=subprocess.run,
) -> tuple[str, str]:
    """``(header_name, header_value)`` for ``cred_broker.CredBroker`` to
    inject for ``harness`` -- the real credential ``auth_broker_args``'s
    placeholder stands in for, read host-side via the exact same login
    ``auth_docker_args`` mounts (never a duplicate/second read of it).

    ``header_name`` matches what the broker path's OWN placeholder env
    actually sends upstream (``x-api-key`` for Claude's
    ``ANTHROPIC_API_KEY``, ``Authorization`` for Codex's ``OPENAI_API_KEY``)
    -- not necessarily the scheme the real credential was issued under. See
    the module docstring's Broker section for the mismatch this creates for
    a Claude Code OAuth login, and the staleness caveat for a Codex OAuth
    session. Raises ``AuthUnavailable`` on the exact same "no login here"
    conditions ``auth_docker_args`` does.
    """
    if harness == "claude":
        token = _claude_macos_token(run) if system == "Darwin" else _claude_linux_token(home)
        return "x-api-key", token

    if harness == "codex":
        return "Authorization", f"Bearer {_codex_token(home)}"

    raise ValueError(f"no broker credential reader for harness: {harness!r}")


_OPENCODE_ENV = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_OPENCODE_TEMPLATE = re.compile(r"\{(?:env|file):")
_OPENCODE_CONFIG_NAMES = ("opencode.json", "opencode.jsonc")
_OPENCODE_CAGE_CONFIG_DIR = "/home/agent/.config/opencode"


def opencode2_global_providers(
    *, home: Path, environ: Mapping[str, str] | None = None,
) -> list[tuple[str, dict]]:
    """``(file name, provider section)`` for each global OpenCode config that
    defines providers, in OpenCode's load order.

    Only the global config dir counts (``$XDG_CONFIG_HOME/opencode``, else
    ``~/.config/opencode``). Project configs are deliberately never read: in a
    run they belong to an untrusted program tree. A missing, unreadable or
    malformed file counts as absent.
    """
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME")
    config_dir = Path(xdg) / "opencode" if xdg else home / ".config" / "opencode"
    found: list[tuple[str, dict]] = []
    for name in _OPENCODE_CONFIG_NAMES:
        try:
            doc = json.loads(_strip_jsonc((config_dir / name).read_text()))
        except (OSError, UnicodeError, ValueError):
            continue
        providers = doc.get("provider") if isinstance(doc, dict) else None
        if isinstance(providers, dict) and providers:
            found.append((name, providers))
    return found


def _provider_env_names(provider: object) -> list[str]:
    """Env var names one provider references: ``{env:NAME}`` in any of its
    strings, plus the names in its ``env`` list."""
    names: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            names.extend(_OPENCODE_ENV.findall(node))
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(provider)
    if isinstance(provider, dict) and isinstance(provider.get("env"), list):
        names += [name for name in provider["env"] if isinstance(name, str)]
    return names


def opencode2_has_provider_key(*, home: Path, environ: Mapping[str, str] | None = None) -> bool:
    """Whether a global provider carries a key the sandbox can use: a literal
    ``options.apiKey``, or an env var the provider references that is set.
    A ``{file:...}`` key doesn't count -- that file isn't in the container."""
    env = os.environ if environ is None else environ
    for _name, providers in opencode2_global_providers(home=home, environ=env):
        for provider in providers.values():
            if not isinstance(provider, dict):
                continue
            options = provider.get("options")
            api_key = options.get("apiKey") if isinstance(options, dict) else None
            if isinstance(api_key, str) and api_key and not _OPENCODE_TEMPLATE.search(api_key):
                return True
            if any(name in env for name in _provider_env_names(provider)):
                return True
    return False


def _write_cage_config(home: Path, name: str, providers: dict) -> Path | None:
    """Write ``{"provider": providers}`` where the container can mount it.

    Owner-only, because a provider may hold a literal key; replaced atomically
    on every call, so each run mounts the config as it is now. ``None`` when
    the file can't be written.
    """
    target = home / ".nethackers" / "opencode2" / name
    tmp: str | None = None
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{name}.")
        with os.fdopen(fd, "w") as f:
            json.dump({"provider": providers}, f)
        os.replace(tmp, target)
    except OSError:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return None
    return target


def _opencode2_docker_args(home: Path, *, environ: Mapping[str, str] | None) -> list[str]:
    """Give OpenCode 2 its provider definitions and nothing else.

    Each global config's ``provider`` section is copied to an owner-only file
    and mounted read-only in place of the original, so plugins, MCP servers
    and instructions never reach the sandbox. The env vars those providers
    reference are forwarded by name (Docker's ``-e NAME`` copies the value
    from this process, keeping it out of argv). ``OPENCODE_DISABLE_PROJECT_CONFIG``
    stops OpenCode reading the worktree's own config, which could otherwise
    redirect a provider -- and the forwarded key -- elsewhere.
    """
    env = os.environ if environ is None else environ
    args: list[str] = []
    names: set[str] = set()
    for name, providers in opencode2_global_providers(home=home, environ=env):
        cage_config = _write_cage_config(home, name, providers)
        if cage_config is None:
            continue
        args += ["-v", f"{cage_config}:{_OPENCODE_CAGE_CONFIG_DIR}/{name}:ro"]
        for provider in providers.values():
            names.update(_provider_env_names(provider))
    args += ["-e", "OPENCODE_DISABLE_PROJECT_CONFIG=1"]
    for name in sorted(names):
        if name in env:
            args += ["-e", name]
    return args


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments without touching comment-like text in strings."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and text[i:i + 2] != "*/":
                i += 1
            i += 2
        else:
            out.append(ch)
            i += 1
    # OpenCode accepts trailing commas in .jsonc. Remove them with another
    # string-aware pass (a regex would corrupt a legitimate string like
    # ``"literal,}"``).
    clean = "".join(out)
    out = []
    i = 0
    in_string = False
    escaped = False
    while i < len(clean):
        ch = clean[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            j = i + 1
            while j < len(clean) and clean[j].isspace():
                j += 1
            if j < len(clean) and clean[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _claude_macos_token(run) -> str:
    """Read the Claude Code Keychain item host-side and return the raw OAuth
    access token. The one place that shells out to ``security`` -- both
    ``_claude_macos_env`` (the mount path's env arg) and ``broker_credential``
    (the broker path's ``header_value``) call this instead of each carrying
    their own copy of the subprocess call.
    """
    result = run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    try:
        return json.loads(result.stdout)["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT) from exc


def _claude_macos_env(run) -> list[str]:
    """The container-only ``CLAUDE_CODE_OAUTH_TOKEN`` env arg. Never touches
    the host shell/file -- the token only ever flows into the container's
    ``-e``.
    """
    return ["-e", f"CLAUDE_CODE_OAUTH_TOKEN={_claude_macos_token(run)}"]


def _claude_linux_token(home: Path) -> str:
    """Read the OAuth access token out of the same ``.credentials.json``
    ``auth_docker_args`` mounts whole on Linux -- the broker path needs the
    value itself, not a mount of the file."""
    creds = home / ".claude" / ".credentials.json"
    try:
        token = json.loads(creds.read_text())["claudeAiOauth"]["accessToken"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT) from exc
    if not token:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    return token


def _codex_token(home: Path) -> str:
    """Read a usable OpenAI credential out of the host's canonical
    ``~/.codex`` -- the same directory ``auth_docker_args`` mounts whole.
    Prefers a stable ``OPENAI_API_KEY`` login; falls back to the OAuth
    session's rotating ``access_token`` (see the module docstring's Broker
    section for the staleness this creates on a long mutator run)."""
    try:
        doc = json.loads((home / ".codex" / "auth.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry") from exc
    api_key = doc.get("OPENAI_API_KEY") if isinstance(doc, dict) else None
    if isinstance(api_key, str) and api_key:
        return api_key
    tokens = doc.get("tokens") if isinstance(doc, dict) else None
    access_token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if isinstance(access_token, str) and access_token:
        return access_token
    raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
