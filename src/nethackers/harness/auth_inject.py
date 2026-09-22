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
gets the broker its real ``HeaderRewrite``, reusing this module's exact
login paths (macOS Keychain / ``.credentials.json`` for Claude, ``~/.codex``
for Codex) rather than duplicating them. ``ContainerOperator``'s ``broker``
flag chooses between the two; the mount stays the default -- see its
docstring for why.

**Claude broker auth is verified live.** Claude Code authenticates its OAuth
token as ``Authorization: Bearer`` (not ``x-api-key``), paired with an
``anthropic-beta`` list -- including an ``oauth-*`` flag -- that the CLI emits
itself. So the broker path runs the caged CLI in OAuth mode
(``CLAUDE_CODE_OAUTH_TOKEN`` placeholder, not ``ANTHROPIC_API_KEY``): it sends
``Bearer <placeholder>`` plus those beta headers, and ``CredBroker`` replaces
only the ``Authorization`` value with the real Bearer, forwarding the beta and
version headers unchanged. Confirmed end-to-end against api.anthropic.com.

One live-verification concern remains (PARKED, not a correctness claim here):

- **Codex token staleness.** ``~/.codex/auth.json``'s OAuth ``access_token``
  (the fallback when there's no stable ``OPENAI_API_KEY`` login) rotates;
  ``broker_credential`` reads it once, at container start, and the broker
  keeps injecting that same snapshot for the container's whole (up to 8h)
  lifetime. The mount path sidesteps this entirely by sharing the live,
  self-refreshing file instead of a value copied out of it once.

**OpenCode 2's broker path is per-provider.** OpenCode's base-URL override is
a per-provider JSON config field (``options.baseURL``), not a single env var
``auth_broker_args`` could point at one broker independent of which
(arbitrary) provider is configured -- so ``auth_broker_args`` itself stays
claude/codex-only and still raises for ``harness="opencode2"``. OpenCode 2
gets its own pair of functions instead: ``opencode2_broker_targets``
resolves, for every provider across the global configs, whether it's
brokerable and the ``(upstream, rewrite)`` to broker it with -- a literal
or env-sourced ``apiKey`` (a ``{file:...}`` key, or no key at all, is left
alone); an explicit ``options.baseURL``, or absent one,
Anthropic's/OpenAI's own default host for those two provider names
specifically (Anthropic gets ``x-api-key``, everything else
``Authorization: Bearer`` -- these are provider API keys from the config,
not Claude Code's OAuth token).
``opencode2_broker_docker_args`` then writes the cage config with each
brokered provider's ``baseURL``/``apiKey`` replaced by the broker's own base
URL and the ``"proxy-managed"`` placeholder -- forwarding by name only the
env vars of providers that stayed unbrokered, since forwarding a brokered
provider's own key var would hand the container that value directly,
bypassing the broker entirely. ``ContainerOperator`` starts one
``cred_broker.CredBroker`` per target ``opencode2_broker_targets`` returns
and wires their base URLs straight into ``opencode2_broker_docker_args``; a
config with no brokerable provider at all falls back to the credential
mount wholesale rather than start zero brokers around an empty config.
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
from urllib.parse import urlsplit

from nethackers.harness.cred_broker import HeaderRewrite

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
        # OAuth mode via CLAUDE_CODE_OAUTH_TOKEN (not ANTHROPIC_API_KEY): Claude
        # Code then sends `Authorization: Bearer <placeholder>` plus the oauth-*
        # anthropic-beta headers its token requires, and the broker replaces only
        # the Authorization value with the real Bearer (broker_credential). The
        # beta/version headers come from Claude and forward through the broker
        # unchanged -- verified with a live round-trip to api.anthropic.com.
        return ["-e", f"ANTHROPIC_BASE_URL={broker_base}",
                "-e", "CLAUDE_CODE_OAUTH_TOKEN=proxy-managed"]

    if harness == "codex":
        return ["-e", f"OPENAI_BASE_URL={broker_base}", "-e", "OPENAI_API_KEY=proxy-managed"]

    if harness == "opencode2":
        # OpenCode's base-URL override is a per-provider JSON config field
        # (`options.baseURL`), not the single (harness, broker_base) env pair
        # this function assumes -- deliberately not guessed here. The real
        # opencode2 broker path goes through `opencode2_broker_targets` /
        # `opencode2_broker_docker_args` instead (one broker per brokerable
        # provider); `ContainerOperator` calls those directly for opencode2
        # and never reaches this branch.
        raise NotImplementedError(
            "opencode2 has no single-broker-base form -- use "
            "opencode2_broker_targets/opencode2_broker_docker_args instead"
        )

    raise ValueError(f"unknown harness: {harness!r}")


def broker_credential(
    harness: str, *, system: str, home: Path, run=subprocess.run,
) -> HeaderRewrite:
    """A ``HeaderRewrite`` for ``cred_broker.CredBroker`` to apply for
    ``harness`` -- the real credential ``auth_broker_args``'s placeholder
    stands in for, read host-side via the exact same login
    ``auth_docker_args`` mounts (never a duplicate/second read of it).

    Both claude and codex inject ``Authorization`` (a Bearer), the header
    the caged CLI's placeholder makes it send, which ``CredBroker`` then
    replaces with the real value. See the module docstring's Broker section
    for the Codex token-staleness caveat. Raises ``AuthUnavailable`` on the
    exact same "no login here" conditions ``auth_docker_args`` does.
    """
    if harness == "claude":
        # Claude Code sends its OAuth token as a Bearer, not x-api-key, so the
        # broker replaces the Authorization header the caged CLI sends (a
        # placeholder Bearer) with the real one. Verified live.
        token = _claude_macos_token(run) if system == "Darwin" else _claude_linux_token(home)
        return HeaderRewrite(inject=(("Authorization", f"Bearer {token}"),))

    if harness == "codex":
        return HeaderRewrite(inject=(("Authorization", f"Bearer {_codex_token(home)}"),))

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


# --- opencode2 per-provider credential broker (§3d, INV2) -------------------
#
# See this module's docstring ("OpenCode 2's broker path is per-provider").
# `opencode2_broker_targets` is the resolve-only half: for each provider
# across the global configs, is there a real key AND somewhere to send it?
# `opencode2_broker_docker_args` (below, alongside `_opencode2_docker_args`)
# is the half that actually rewrites the cage config once brokers exist for
# whichever targets this returned.

_OPENCODE2_DEFAULT_UPSTREAMS = {
    # (upstream, header_name, is_bearer) for a provider with NO explicit
    # `options.baseURL`, keyed by provider name -- the only two names this
    # module knows a real default host for. Any other unrecognized name
    # without a `baseURL` has nowhere known to broker it TO and is left
    # alone (see `_opencode2_provider_upstream`).
    "anthropic": ("https://api.anthropic.com", "x-api-key", False),
    "openai": ("https://api.openai.com/v1", "Authorization", True),
}


def _opencode2_provider_key(provider: dict, env: Mapping[str, str]) -> str | None:
    """The real key value for one provider, or ``None`` if it can't be used
    in the cage: a literal ``options.apiKey`` is returned as-is; an
    ``{env:NAME}`` one resolves through ``env`` (``None`` if ``NAME`` isn't
    set -- the provider is simply left unbrokered, same as if it had no key
    at all); anything else -- ``{file:...}``, a non-string, or absent --
    isn't brokerable (a file path doesn't exist in the container, and
    nothing else here is safe to guess at)."""
    options = provider.get("options")
    api_key = options.get("apiKey") if isinstance(options, dict) else None
    if not isinstance(api_key, str) or not api_key:
        return None
    env_ref = _OPENCODE_ENV.fullmatch(api_key)
    if env_ref:
        return env.get(env_ref.group(1))
    if _OPENCODE_TEMPLATE.search(api_key):
        return None   # e.g. {file:...} -- that file isn't in the container
    return api_key


def _opencode2_provider_upstream(name: str, provider: dict) -> tuple[str, str, bool] | None:
    """``(upstream, header_name, is_bearer)`` for one provider, or ``None``
    if there's nowhere known to broker it to.

    An explicit ``options.baseURL`` always wins and becomes the upstream;
    the header is ``x-api-key`` when the provider is named ``anthropic`` or
    its base URL's host ends in ``anthropic.com`` (an Anthropic-compatible
    endpoint under a different provider name), ``Authorization: Bearer``
    otherwise. Without a ``baseURL``, only ``anthropic``/``openai`` --
    ``_OPENCODE2_DEFAULT_UPSTREAMS`` -- are brokerable; any other name is
    left alone (it keeps the mount/forward path) rather than guessed at.
    """
    options = provider.get("options")
    base_url = options.get("baseURL") if isinstance(options, dict) else None
    if isinstance(base_url, str) and base_url:
        host = urlsplit(base_url).hostname or ""
        if name == "anthropic" or host == "anthropic.com" or host.endswith(".anthropic.com"):
            return base_url, "x-api-key", False
        return base_url, "Authorization", True
    return _OPENCODE2_DEFAULT_UPSTREAMS.get(name)


def opencode2_broker_targets(
    *, home: Path, environ: Mapping[str, str] | None = None,
) -> list[dict]:
    """One entry per BROKERABLE provider across the global OpenCode configs
    (``opencode2_global_providers``): ``{"file", "name", "upstream",
    "rewrite"}``, ready to hand straight to
    ``cred_broker.CredBroker(upstream, rewrite)``.

    A provider is brokerable only when BOTH halves resolve --
    ``_opencode2_provider_key`` (the real value ``rewrite`` injects) and
    ``_opencode2_provider_upstream`` (where to send it and under what header
    name). A provider with a ``{file:...}``/absent key, an unset
    ``{env:NAME}``, or an unrecognized name with no ``baseURL`` is simply
    absent from the result -- it keeps the existing mount/forward path
    instead, never an error. Order follows ``opencode2_global_providers``'
    (file load order, then dict order within each file).
    """
    env = os.environ if environ is None else environ
    targets: list[dict] = []
    for file_name, providers in opencode2_global_providers(home=home, environ=env):
        for provider_name, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            key = _opencode2_provider_key(provider, env)
            if key is None:
                continue
            resolved = _opencode2_provider_upstream(provider_name, provider)
            if resolved is None:
                continue
            upstream, header_name, is_bearer = resolved
            header_value = f"Bearer {key}" if is_bearer else key
            targets.append({
                "file": file_name,
                "name": provider_name,
                "upstream": upstream,
                "rewrite": HeaderRewrite(inject=((header_name, header_value),)),
            })
    return targets


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


def _opencode2_brokered_provider(provider: dict, broker_base: str) -> dict:
    """``provider`` with its ``options.baseURL``/``options.apiKey``
    overwritten to point at the broker -- everything else about it (models,
    its own ``env`` list, any other ``options`` field) carried over
    unchanged. The pair this overwrites is exactly what
    ``_opencode2_provider_upstream``/``_opencode2_provider_key`` resolved to
    build the broker ``broker_base`` is the (host-gateway) address of."""
    options = provider.get("options")
    new_options = dict(options) if isinstance(options, dict) else {}
    new_options["baseURL"] = broker_base
    new_options["apiKey"] = "proxy-managed"
    rewritten = dict(provider)
    rewritten["options"] = new_options
    return rewritten


def opencode2_broker_docker_args(
    home: Path,
    *,
    environ: Mapping[str, str] | None = None,
    broker_bases: Mapping[tuple[str, str], str],
) -> list[str]:
    """Broker-path counterpart to ``_opencode2_docker_args``: same cage-config
    mount + env-forwarding shape, except every provider keyed (by ``(file,
    provider name)``) in ``broker_bases`` -- the targets
    ``opencode2_broker_targets`` returned, each now backed by a running
    broker at the given base URL -- is rewritten via
    ``_opencode2_brokered_provider`` to point at its broker instead of the
    real upstream. Non-brokered providers are written through exactly as
    ``_opencode2_docker_args`` would.

    Env-forwarding covers ONLY providers NOT in ``broker_bases``. A brokered
    provider's real key must never reach the container -- neither as the
    literal ``_opencode2_brokered_provider`` already replaced, nor (the part
    that matters here) as the env var it might have come from: forwarding
    that var by name would hand the container the value straight out of
    this process's environment, right past the broker that exists
    specifically to keep it out.
    """
    env = os.environ if environ is None else environ
    args: list[str] = []
    names: set[str] = set()
    for file_name, providers in opencode2_global_providers(home=home, environ=env):
        rewritten: dict[str, object] = {}
        for provider_name, provider in providers.items():
            broker_base = broker_bases.get((file_name, provider_name))
            if broker_base is not None and isinstance(provider, dict):
                rewritten[provider_name] = _opencode2_brokered_provider(provider, broker_base)
            else:
                rewritten[provider_name] = provider
        cage_config = _write_cage_config(home, file_name, rewritten)
        if cage_config is None:
            continue
        args += ["-v", f"{cage_config}:{_OPENCODE_CAGE_CONFIG_DIR}/{file_name}:ro"]
        for provider_name, provider in providers.items():
            if (file_name, provider_name) in broker_bases:
                continue   # brokered -- its env var (if any) must not be forwarded
            if isinstance(provider, dict):
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
    (the broker path's ``rewrite``) call this instead of each carrying
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
