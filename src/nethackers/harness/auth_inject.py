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
real credential, it hands it a placeholder plus a way to reach
``cred_broker.CredBroker`` -- which holds the real credential host-side and
injects it only into requests it forwards to the one provider host. For
Claude that's a placeholder token + a base-URL env override; for Codex it's a
``-c model_providers.…`` invocation override (in the ``codex exec`` command,
not this module) pointing codex at a CUSTOM UNAUTHENTICATED provider, so codex
sends NO credential and the broker injects it all -- this module's codex
branch supplies only an EMPTY, writable cage ``~/.codex`` + ``CODEX_HOME``.
``broker_credential`` is the host-side read that gets the broker
its real ``HeaderRewrite``, reusing this module's exact login paths (macOS
Keychain / ``.credentials.json`` for Claude, ``~/.codex`` for Codex) rather
than duplicating them. ``ContainerOperator``'s ``broker`` flag chooses
between the two; the mount stays the default -- see its docstring for why.

**Claude broker auth is verified live.** Claude Code authenticates its OAuth
token as ``Authorization: Bearer`` (not ``x-api-key``), paired with an
``anthropic-beta`` list -- including an ``oauth-*`` flag -- that the CLI emits
itself. So the broker path runs the caged CLI in OAuth mode
(``CLAUDE_CODE_OAUTH_TOKEN`` placeholder, not ``ANTHROPIC_API_KEY``): it sends
``Bearer <placeholder>`` plus those beta headers, and ``CredBroker`` replaces
the ``Authorization`` value with the real Bearer, strips any inbound
``x-api-key`` (so a smuggled key can't shadow the injected Bearer), and merges
``oauth-2025-04-20`` into ``anthropic-beta`` rather than trusting the CLI to
emit it (version-dependent) -- forwarding the rest of the beta and version
headers unchanged. Confirmed end-to-end against api.anthropic.com. The real
Bearer itself prefers a durable ``setup-token`` (§3.3: a 1-year subscription
OAuth token, read from ``NETHACKERS_CLAUDE_SETUP_TOKEN`` or
``~/.nethackers/claude/setup-token``); with no setup-token it uses the
interactive ``~/.claude`` login and, like codex, REFRESHES that ~8h token
host-side when it is near expiry -- writing the rotated single-use token back
to the Keychain / ``.credentials.json`` (INV B4) -- so an end user logs into
Claude once and the token is kept alive for them, no setup-token chore. The
refresh endpoint (``platform.claude.com``) is Cloudflare-WAF'd, so a host it
blocks (often headless Linux) still falls back to the setup-token.

**Codex broker (chatgpt.com backend + `-c` unauthenticated provider).** A
ChatGPT-subscription codex login (``~/.codex/auth.json`` is ``auth_mode:
chatgpt`` with an empty ``OPENAI_API_KEY`` and a rotating
``tokens.access_token``) POSTs to ``chatgpt.com/backend-api/codex/responses``
and IGNORES ``OPENAI_BASE_URL`` for its model endpoint. Worse, the mutator
runs ``codex exec --ignore-user-config``, which DISCARDS
``~/.codex/config.toml`` -- so an earlier cage that routed via a ``config.toml``
``openai_base_url`` was silently ignored, codex fell back to ``api.openai.com``,
and the call 401'd (NOT an auth/placeholder problem: a routing one). The
CONFIRMED fix (proven in our own E2E, and OpenAI's own ``responses-api-proxy``
recipe) is to route codex by a ``-c`` INVOCATION override -- which applies even
under ``--ignore-user-config`` -- pointing it at a CUSTOM provider named
``nethackers-broker`` with ``base_url`` ending in ``/backend-api/codex`` and no
``requires_openai_auth``/``env_key``. With no auth configured codex uses its
``unauthenticated_auth_provider()`` and sends the POST with NO ``Authorization``
header, so the BROKER injects 100% of the auth and codex needs no credential in
the box. That ``-c`` override lives in the ``codex exec`` command
(``operator._codex_cmd``, threaded via ``container_operator.build_docker_argv``'s
``broker_base``), NOT this module; ``auth_broker_args`` here supplies only the
cage ``~/.codex`` -- now an EMPTY, world-writable directory (container agent
must write it) + ``CODEX_HOME``
(codex needs a writable ``$CODEX_HOME`` for its app-server socket/state; no
token, no config, B1). ``broker_credential`` then injects the real
``Authorization: Bearer`` and, authoritatively, the ``ChatGPT-Account-Id``
header (exact case), preserving codex's own ``originator: codex_cli_rs`` +
``User-Agent`` (a real server-side first-party allowlist -- the broker forwards
every non-injected header unchanged), and refreshes the rotating token
PROACTIVELY by its ``exp`` (``_codex_token_needs_refresh`` / ``_codex_refresh``,
at ``auth.openai.com/oauth/token``) rather than pinning one ~8h snapshot --
writing the rotated single-use token back to the canonical file (the broker is
the sole writer during a broker run; the container has no ``~/.codex`` mount).

Validated on a MOCK provider (``test_broker_e2e`` / ``test_broker_transform``).
The real ``chatgpt.com`` sits behind Cloudflare JA3/TLS fingerprinting that
403s a plain httpx forward, so ``ContainerOperator`` constructs the codex
``CredBroker`` (only) with ``impersonate=True``: a Chrome-TLS-impersonating
forward via ``curl_cffi`` (``cred_broker.CredBroker``) instead of httpx.
``curl_cffi`` is a lazy, host-side-only import -- **running the codex broker
requires ``pip install curl_cffi`` on the host**; it is never a packaged
dependency, so the mutator image/fingerprint stays untouched. Fallback, if a
live smoke shows codex refuses to start against a fully empty ``~/.codex``: a
minimal VALID ``auth.json`` (a 3-part ``id_token`` with a non-empty 3rd
segment) -- not the placeholder-JWT + ``config.toml`` cage this replaced.

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

import base64
import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from nethackers.harness.cred_broker import HeaderRewrite

log = logging.getLogger(__name__)

# `claude auth login` (main's setup recipes point at it) rather than a bare
# `claude` -- the wording ebebdda standardized so doctor/setup hints agree.
_CLAUDE_LOGIN_HINT = "run `claude auth login` on this host, then retry"

# The codex CLI's own public OAuth app id (the id_token `aud` claim) -- not a
# secret, pinned from the codex-rs source. `_CODEX_TOKEN_ENDPOINT` is OpenAI's
# refresh endpoint for that app (§3.3).
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"

# Proactive-refresh margin (§3.3): refresh the rotating codex access token once
# it has less than this left before its JWT `exp`, rather than pinning one ~8h
# snapshot for a container's whole (up to 8h) lifetime.
_CODEX_REFRESH_MARGIN_S = 30 * 60

# Claude Code's own public OAuth client id + Anthropic's refresh endpoint --
# not a secret, pinned from Claude Code's subscription OAuth flow. The claude
# twin of the codex pair above: the broker refreshes the interactive `claude`
# login's ~8h subscription access token host-side (using the refresh token in
# the login credential) and writes the rotated credential BACK, so an end user
# logs into Claude once and never touches a token again -- no per-machine
# `setup-token` chore (§3.3, INV B4). The setup-token stays the operator/fleet
# path for headless boxes with no interactive login.
# client_id: Claude Code's own public OAuth app id (unanimous across every
# community reverse-engineering of the CLI; a refresh token is bound to the
# client_id it was minted under, so this exact value is required). Endpoint:
# Anthropic migrated its console from console.anthropic.com to
# platform.claude.com (the old host 404s), and this endpoint is Cloudflare-
# fronted + WAF'd -- like codex's chatgpt.com -- so a plain httpx POST can be
# 403/429'd from some environments (notably headless Linux); the setup-token
# stays the fallback there. Body is JSON; the response is OAuth snake_case
# (access_token/refresh_token/expires_in seconds) which we map onto the local
# camelCase storage (accessToken/refreshToken/expiresAt ms).
CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_REFRESH_MARGIN_S = 30 * 60
_CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"


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


def auth_broker_args(
    harness: str, *, broker_base: str, home: Path | None = None,
) -> list[str]:
    """Point ``harness`` at the credential broker (``cred_broker.CredBroker``,
    started at ``broker_base``) instead of mounting its host login.

    Claude gets a PLACEHOLDER token plus its ``ANTHROPIC_BASE_URL`` env
    override, no ``-v`` mount. Codex is routed at the broker NOT here but by a
    ``-c model_providers.…`` invocation override in its ``codex exec`` command
    (``container_operator``/``operator._codex_cmd``) -- a ChatGPT-subscription
    login ignores ``OPENAI_BASE_URL`` for its model endpoint, and ``codex exec
    --ignore-user-config`` discards a cage ``config.toml``, so only a ``-c``
    override reaches it. That override is UNAUTHENTICATED, so codex sends no
    credential and the broker injects it all host-side (``broker_credential``).
    All this codex branch supplies is the cage ``~/.codex`` itself: an EMPTY,
    world-writable dir mounted at ``~/.codex`` plus ``CODEX_HOME`` (codex
    needs a writable ``$CODEX_HOME`` for its app-server socket/state) -- so
    ``home`` is REQUIRED for codex, but no token and no config cross the
    boundary. See ``ContainerOperator``'s ``broker`` flag for how a caller opts
    into this instead of ``auth_docker_args``'s mount (the default).
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
        if home is None:
            raise ValueError("auth_broker_args('codex') requires home= for the cage dir")
        return _codex_cage_args(home)

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
    environ: Mapping[str, str] | None = None,
) -> HeaderRewrite:
    """A ``HeaderRewrite`` for ``cred_broker.CredBroker`` to apply for
    ``harness`` -- the real credential ``auth_broker_args``'s placeholder
    stands in for, read host-side via the exact same login
    ``auth_docker_args`` mounts (never a duplicate/second read of it).

    Both claude and codex inject ``Authorization`` (a Bearer), the header
    the caged CLI's placeholder makes it send, which ``CredBroker`` then
    replaces with the real value. Codex additionally injects the host's
    (non-secret) ``account_id`` as ``ChatGPT-Account-Id`` authoritatively and
    refreshes its rotating access token PROACTIVELY by the token's ``exp``
    (see the module docstring's "Codex broker" section). Raises
    ``AuthUnavailable`` on the exact same "no login here" conditions
    ``auth_docker_args`` does.

    Claude additionally strips an inbound ``x-api-key`` and merges
    ``oauth-2025-04-20`` into ``anthropic-beta`` (see the module docstring's
    "Claude broker auth is verified live" section), and prefers a durable
    ``setup-token`` over the ~8h login token when one is provisioned --
    ``environ`` (default ``os.environ``) is where ``NETHACKERS_CLAUDE_SETUP_TOKEN``
    is looked up.
    """
    env = os.environ if environ is None else environ
    if harness == "claude":
        # Claude Code sends its OAuth token as a Bearer, not x-api-key, so the
        # broker replaces the Authorization header the caged CLI sends (a
        # placeholder Bearer) with the real one. Verified live.
        #
        # A durable operator-provisioned setup-token wins when present (headless
        # fleet boxes with no interactive login). Otherwise use the interactive
        # `claude` login and, like codex, REFRESH it host-side when it is near
        # its expiry -- writing the rotated credential back -- so an end user
        # logs into Claude once and the ~8h token is kept alive for them, with
        # no setup-token chore (§3.3, INV B4). See _claude_refresh.
        token = _claude_setup_token(home, env)
        if token is None:
            oauth = _claude_login_doc(home, system=system, run=run)["claudeAiOauth"]
            if _claude_token_needs_refresh(oauth):
                token = _claude_refresh(home, system=system, run=run)
            else:
                token = oauth["accessToken"]
        return HeaderRewrite(
            inject=(("Authorization", f"Bearer {token}"),),
            strip=("x-api-key",),
            merge_csv=(("anthropic-beta", ("oauth-2025-04-20",)),),
        )

    if harness == "codex":
        # ChatGPT-subscription login: read the rotating OAuth access_token from
        # ~/.codex/auth.json, refresh it PROACTIVELY when it's near its exp (or
        # not a decodable JWT), inject the real Bearer, and inject the host's
        # (non-secret) account_id as ChatGPT-Account-Id AUTHORITATIVELY -- the
        # broker's value wins regardless of how codex derives it (account_id is
        # not a secret; B1). See the module docstring's "Codex broker" section.
        creds = _codex_creds(home)
        raw_tokens = creds.get("tokens")
        tokens = raw_tokens if isinstance(raw_tokens, dict) else {}
        account_id = tokens.get("account_id")
        access = tokens.get("access_token")
        if not (isinstance(access, str) and access) or _codex_token_needs_refresh(access):
            access = _codex_refresh(home)   # writes the rotated token back; returns fresh access
        inject: tuple[tuple[str, str], ...] = (("Authorization", f"Bearer {access}"),)
        if isinstance(account_id, str) and account_id:
            inject = inject + (("ChatGPT-Account-Id", account_id),)
        return HeaderRewrite(inject=inject)

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


def _claude_setup_token(home: Path, environ: Mapping[str, str]) -> str | None:
    """A durable Claude Code `setup-token` (1-year OAuth), if the operator provisioned one:
    the `NETHACKERS_CLAUDE_SETUP_TOKEN` env var wins, else `~/.nethackers/claude/setup-token`.
    A distinct, out-of-band credential -- NOT `~/.claude`, which is the interactive login the
    broker's fallback path reads and refreshes on its own (rotating single-use token written
    back -- INV B4). The setup-token is the durable path for headless boxes with no
    interactive login (or where the WAF blocks the refresh)."""
    env_tok = environ.get("NETHACKERS_CLAUDE_SETUP_TOKEN")
    if env_tok:
        return env_tok
    path = home / ".nethackers" / "claude" / "setup-token"
    try:
        tok = path.read_text().strip()
    except OSError:
        return None
    return tok or None


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


def _claude_login_doc(home: Path, *, system: str, run=subprocess.run) -> dict:
    """The full parsed interactive-`claude`-login credential doc -- the object
    that wraps ``claudeAiOauth`` -- read from the Keychain item on macOS or
    ``~/.claude/.credentials.json`` on Linux. The broker's refresh path reads
    this (for the refresh token) and writes the whole doc BACK, preserving any
    sibling keys. Guarantees ``doc['claudeAiOauth']`` is a dict; raises
    ``AuthUnavailable`` on a missing / unreadable / malformed login."""
    if system == "Darwin":
        result = run(
            ["security", "find-generic-password", "-s", _CLAUDE_KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
        raw = result.stdout
    else:
        try:
            raw = (home / ".claude" / ".credentials.json").read_text()
        except OSError as exc:
            raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT) from exc
    try:
        doc = json.loads(raw)
        oauth = doc["claudeAiOauth"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT) from exc
    if not isinstance(doc, dict) or not isinstance(oauth, dict):
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    return doc


def _claude_token_needs_refresh(oauth: Mapping) -> bool:
    """Whether the interactive-login ``claudeAiOauth`` credential should be
    refreshed before the broker injects it -- the claude twin of
    ``_codex_token_needs_refresh``. True when its ``accessToken`` is absent, or
    its ``expiresAt`` (epoch ms) is within ``_CLAUDE_REFRESH_MARGIN_S`` of now.
    A credential whose ``expiresAt`` can't be read is used AS-IS (False): a real
    Claude Code login always carries one, so a missing/odd value is a legacy or
    test shape we don't force-rotate (which could fail if there's also no
    refresh token) -- unlike codex's JWT, where an unreadable exp means refresh.
    """
    access = oauth.get("accessToken")
    if not (isinstance(access, str) and access):
        return True
    exp = oauth.get("expiresAt")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return False
    exp_s = exp / 1000 if exp > 1e12 else exp
    return exp_s - time.time() < _CLAUDE_REFRESH_MARGIN_S


def _claude_keychain_account(run) -> str:
    """The macOS Keychain ``account`` attribute the refresh write-back
    (``security add-generic-password -U -a``) must target, read from the
    existing item so the update lands on it instead of creating a duplicate."""
    result = run(
        ["security", "find-generic-password", "-s", _CLAUDE_KEYCHAIN_SERVICE],
        capture_output=True, text=True,
    )
    match = re.search(r'"acct"<blob>="((?:[^"\\]|\\.)*)"', getattr(result, "stdout", "") or "")
    if not match:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    return match.group(1)


def _claude_write_login_doc(home: Path, *, system: str, run, doc: dict) -> None:
    """Persist the refreshed login doc back where it lives -- the Keychain item
    on macOS, ``~/.claude/.credentials.json`` on Linux -- so the rotated
    (single-use) refresh token isn't lost and Claude Code's own next refresh
    reads the same rotated pair (INV B4). Raises ``AuthUnavailable`` on failure.
    """
    blob = json.dumps(doc)
    if system == "Darwin":
        # -w passes the blob on argv (visible to a host `ps`) -- host-local and
        # OUTSIDE the broker's threat boundary (the untrusted container never
        # sees host process args; the token already lives in this Keychain).
        account = _claude_keychain_account(run)
        result = run(
            ["security", "add-generic-password", "-U",
             "-a", account, "-s", _CLAUDE_KEYCHAIN_SERVICE, "-w", blob],
            capture_output=True, text=True,
        )
        if getattr(result, "returncode", 1) != 0:
            raise AuthUnavailable(
                "claude",
                "could not save the refreshed claude token to the Keychain -- run "
                "`claude` to log in again, then retry",
            )
        return
    # Linux: atomic, owner-only write-back (mirrors _codex_refresh's auth.json).
    target = home / ".claude" / ".credentials.json"
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".credentials.json.")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(blob)
        os.replace(tmp, target)
    except OSError as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise AuthUnavailable(
            "claude",
            "could not save the refreshed claude token -- run `claude` to log in again, "
            "then retry",
        ) from exc


def _claude_refresh(home: Path, *, system: str, run=subprocess.run, post=httpx.post) -> str:
    """Refresh the interactive `claude` login's ~8h subscription access token
    via its refresh token and WRITE THE ROTATED CREDENTIAL BACK (Keychain on
    macOS, ``~/.claude/.credentials.json`` on Linux). The claude twin of
    ``_codex_refresh``: the refresh token is single-use, so the new pair must be
    persisted before this returns or the next refresh -- ours OR Claude Code's
    own -- is locked out (INV B4). Returns the new access token. ``run``/``post``
    are injectable for tests; raises ``AuthUnavailable`` on any failure, leaving
    the stored credential unchanged (nothing is written unless the refresh
    actually succeeded).

    ``_CLAUDE_TOKEN_ENDPOINT`` is Cloudflare-WAF'd: a non-200 here is often a
    transient reachability / bot-classification block (notably on headless
    Linux), NOT a dead login -- the message says so, and the setup-token stays
    the durable fallback for a host the WAF won't let refresh.
    """
    doc = _claude_login_doc(home, system=system, run=run)
    oauth = doc["claudeAiOauth"]
    refresh_token = oauth.get("refreshToken")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise AuthUnavailable("claude", _CLAUDE_LOGIN_HINT)
    try:
        resp = post(
            _CLAUDE_TOKEN_ENDPOINT,
            json={
                "grant_type": "refresh_token",
                "client_id": CLAUDE_OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise AuthUnavailable(
            "claude",
            "claude token refresh could not reach the OAuth endpoint -- check the "
            "network, then retry",
        ) from exc
    if resp.status_code != 200:
        raise AuthUnavailable(
            "claude",
            "claude token refresh failed -- if this host is behind a strict egress / WAF "
            "(often headless Linux) provision a setup-token; otherwise run `claude` to "
            "log in again, then retry",
        )
    try:
        payload = resp.json()
    except (ValueError, TypeError) as exc:
        raise AuthUnavailable(
            "claude",
            "claude token refresh returned an unreadable response -- run `claude` to log "
            "in again, then retry",
        ) from exc
    if not isinstance(payload, dict):
        raise AuthUnavailable(
            "claude",
            "claude token refresh returned an unexpected response -- run `claude` to log "
            "in again, then retry",
        )
    new_access = payload.get("access_token")
    if not isinstance(new_access, str) or not new_access:
        raise AuthUnavailable(
            "claude",
            "claude token refresh returned no access_token -- run `claude` to log in "
            "again, then retry",
        )
    new_oauth = dict(oauth)
    new_oauth["accessToken"] = new_access
    new_oauth["refreshToken"] = payload.get("refresh_token") or refresh_token  # keep old if omitted
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        new_oauth["expiresAt"] = int((time.time() + expires_in) * 1000)
    new_doc = dict(doc)
    new_doc["claudeAiOauth"] = new_oauth
    _claude_write_login_doc(home, system=system, run=run, doc=new_doc)
    return new_access


def _codex_creds(home: Path) -> dict:
    """Parsed ``~/.codex/auth.json`` -- the same file ``auth_docker_args``
    mounts whole and ``_codex_refresh`` / the codex broker path read. Raises
    ``AuthUnavailable`` on a missing, unreadable, or malformed (unparsable,
    or not a JSON object) file."""
    try:
        doc = json.loads((home / ".codex" / "auth.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry") from exc
    if not isinstance(doc, dict):
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
    return doc


def _codex_refresh(home: Path, *, post=httpx.post) -> str:
    """Refresh the rotating ChatGPT-subscription OAuth access token (§3.3)
    and WRITE IT BACK to ``~/.codex/auth.json``: the refresh token is
    single-use, so unless the new one is persisted, the *next* refresh --
    including the host's own interactive ``codex`` login -- fails and the
    login bricks. Returns the new ``access_token``.

    ``post`` is an injectable ``httpx.post``-shaped callable (positional
    ``url``, keyword ``data``), faked in tests so this never touches the
    network. Raises ``AuthUnavailable`` on any failure -- no refresh token to
    send, a non-200 response, or a 200 response missing ``access_token`` --
    leaving the file byte-for-byte unchanged (INV B4: nothing here writes
    unless the refresh actually succeeded).
    """
    doc = _codex_creds(home)
    tokens = doc.get("tokens")
    if not isinstance(tokens, dict):
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        # Guard against a malformed (non-string) or absent refresh_token, which
        # would otherwise be handed to httpx as-is and surface as a raw error
        # (same shape as the access_token isinstance guards elsewhere).
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry")

    try:
        resp = post(_CODEX_TOKEN_ENDPOINT, data={
            "grant_type": "refresh_token",
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
        })
    except httpx.HTTPError as exc:
        # A connect/timeout/transport failure -- a live broker run calls this,
        # so surface the friendly AuthUnavailable, not a raw httpx exception.
        raise AuthUnavailable(
            "codex",
            "codex token refresh could not reach the OAuth endpoint -- check the "
            "network, then retry",
        ) from exc
    if resp.status_code != 200:
        raise AuthUnavailable(
            "codex", "codex token refresh failed -- run `codex login` on this host, then retry"
        )
    try:
        payload = resp.json()
    except (ValueError, TypeError) as exc:
        raise AuthUnavailable(
            "codex",
            "codex token refresh returned an unreadable response -- run `codex login`, "
            "then retry",
        ) from exc
    if not isinstance(payload, dict):
        raise AuthUnavailable(
            "codex",
            "codex token refresh returned an unexpected response -- run `codex login`, "
            "then retry",
        )
    new_access = payload.get("access_token")
    new_refresh = payload.get("refresh_token") or refresh_token  # some providers omit a new one
    if not new_access:
        raise AuthUnavailable(
            "codex",
            "codex token refresh returned no access_token -- run `codex login`, then retry",
        )

    tokens = dict(tokens)
    tokens["access_token"] = new_access
    tokens["refresh_token"] = new_refresh
    if "id_token" in payload:
        tokens["id_token"] = payload["id_token"]
    new_doc = dict(doc)
    new_doc["tokens"] = tokens

    # Atomic, owner-only write-back (mirrors _write_cage_config's mkstemp +
    # os.replace): the refresh token is single-use, so a torn/partial write
    # here is as bad as not writing at all -- either way the next refresh
    # (ours or the host's own `codex` login) is locked out until the user
    # re-runs `codex login`.
    target = home / ".codex" / "auth.json"
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".auth.json.")
        with os.fdopen(fd, "w") as f:
            json.dump(new_doc, f)
        os.replace(tmp, target)
    except OSError as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise AuthUnavailable(
            "codex",
            "could not save the refreshed codex token -- run `codex login` on this host, "
            "then retry",
        ) from exc
    return new_access


def _codex_token_needs_refresh(token: str) -> bool:
    """Whether ``token`` -- a codex OAuth access token, expected to be a JWT --
    is close enough to expiry to refresh proactively (§3.3). True when its
    ``exp`` is within ``_CODEX_REFRESH_MARGIN_S`` of now, OR the token can't be
    decoded as a JWT carrying a numeric ``exp``. "Refresh when uncertain" is
    the safe default: a token we can't reason about is treated as one that may
    already be stale, so the broker refreshes rather than inject a dead token.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        )
        exp = payload["exp"]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return True
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return True
    return exp - time.time() < _CODEX_REFRESH_MARGIN_S


def _codex_cage_args(home: Path) -> list[str]:
    """Build the codex broker cage and return its ``-v`` mount + ``CODEX_HOME``
    env. Routing and auth no longer live in the cage: codex is pointed at the
    broker by a ``-c model_providers.…`` INVOCATION override
    (``container_operator``/``operator._codex_cmd``), which applies even under
    ``codex exec --ignore-user-config`` (that flag discards
    ``~/.codex/config.toml`` -- the reason the earlier cage ``config.toml``
    ``openai_base_url`` was silently ignored, codex fell back to
    ``api.openai.com``, and the call 401'd). The override carries no
    ``requires_openai_auth``/``env_key``, so codex uses its unauthenticated
    auth provider and sends the POST with NO ``Authorization``; the real
    ``Authorization: Bearer`` + ``ChatGPT-Account-Id`` are injected on the wire
    by the broker (``broker_credential``). So NO token and NO config ever enter
    the cage (B1).

    The cage is therefore an EMPTY, world-writable directory mounted read-WRITE at
    ``~/.codex`` with ``CODEX_HOME`` pointed at it: docker creates a bind-mount's
    parent dir root-owned, but the mutator entrypoint drops to the ``agent``
    user, so codex needs a dir it can actually write its app-server
    socket/session state into ("Permission denied", os error 13 otherwise --
    confirmed by the real E2E). Any stale ``auth.json``/``config.toml`` left by
    an older code version is removed so the cage genuinely carries neither.

    (Fallback, if a live smoke shows codex refuses to start against a fully
    empty ``~/.codex``: a minimal VALID ``auth.json`` -- a 3-part ``id_token``
    with a non-empty 3rd segment -- rather than the placeholder-JWT +
    ``config.toml`` cage this replaced.)
    """
    cage_dir = home / ".nethackers" / "codex-cage"
    try:
        cage_dir.mkdir(parents=True, exist_ok=True)
        # World-writable ON PURPOSE: on native Linux docker the bind-mount is
        # root-owned but the mutator entrypoint drops to the `agent` user (uid
        # 1000), which MUST write codex's app-server socket/session state here
        # -- "Permission denied" (os error 13) otherwise, confirmed on a real
        # Linux box (Docker Desktop UID-maps the volume, so macOS never hit
        # it). Safe: the cage is EMPTY and non-secret -- no token, no config
        # (B1) -- so nothing sensitive is ever written into it. chmod (not
        # mkdir mode=) so it holds regardless of the process umask.
        os.chmod(cage_dir, 0o777)
        # Defensive: drop the two files an older (placeholder-JWT + config.toml)
        # cage wrote, so an upgraded host's cage is empty as promised. Codex's
        # own runtime state (sockets, etc.) is left untouched.
        for stale in ("auth.json", "config.toml"):
            with contextlib.suppress(OSError):
                (cage_dir / stale).unlink()
    except OSError as exc:
        raise AuthUnavailable(
            "codex",
            "could not create the codex broker cage dir -- check ~/.nethackers "
            "permissions, then retry",
        ) from exc
    # A single writable dir mount (NOT two :ro files) + CODEX_HOME; the real
    # Bearer is swapped on the wire by broker_credential, never mounted here.
    return ["-v", f"{cage_dir}:/home/agent/.codex", "-e", "CODEX_HOME=/home/agent/.codex"]
