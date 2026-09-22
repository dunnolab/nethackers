import base64
import json
import logging
import os
import time
from pathlib import Path

import httpx
import pytest

from nethackers.harness import auth_inject
from nethackers.harness.auth_inject import (
    AuthUnavailable,
    auth_docker_args,
    opencode2_has_provider_key,
)
from nethackers.harness.cred_broker import HeaderRewrite


def _b64url_json(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode("ascii")


def _make_jwt(exp, **claims) -> str:
    """A structurally-valid unsigned (``alg:none``) JWT carrying ``exp`` (plus
    any extra ``claims``) -- enough for the token-staleness / cage-login tests
    to reason about, never a real token."""
    header = _b64url_json({"alg": "none", "typ": "JWT"})
    payload = _b64url_json({"exp": exp, **claims})
    return f"{header}.{payload}."


def test_codex_mounts_the_one_canonical_dir():
    args = auth_docker_args("codex", system="Linux", home=Path("/h"))
    assert args == ["-v", "/h/.codex:/home/agent/.codex"]


def _opencode_config(home: Path, text: str, name: str = "opencode.json") -> Path:
    path = home / ".config" / "opencode" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _mounted_ro(args: list[str], target: str) -> Path:
    """The host file bind-mounted read-only at ``target`` in ``args``."""
    for flag, value in zip(args, args[1:], strict=False):
        if flag == "-v" and value.endswith(f":{target}:ro"):
            return Path(value.removesuffix(f":{target}:ro"))
    raise AssertionError(f"nothing mounted read-only at {target}: {args}")


def test_opencode2_sandbox_gets_only_the_provider_section_of_the_global_config(tmp_path):
    # Plugins and MCP servers execute, instructions steer the agent: none of it
    # may follow the user's interactive setup into a mutation. Providers may.
    _opencode_config(tmp_path, """{
      // OpenCode reads JSONC, trailing commas included
      "provider": {"custom": {"options": {"apiKey": "{env:CUSTOM_KEY}"},
                              "models": {"m1": {"variants": {"high": {}}}}}},
      "plugin": ["memory-plugin"],
      "mcp": {"memory": {"type": "remote", "url": "https://mcp.example"}},
      "instructions": ["~/rules.md"],
    }""")

    args = auth_docker_args("opencode2", system="Linux", home=tmp_path, environ={})

    mounted = _mounted_ro(args, "/home/agent/.config/opencode/opencode.json")
    assert json.loads(mounted.read_text()) == {"provider": {"custom": {
        "options": {"apiKey": "{env:CUSTOM_KEY}"},
        "models": {"m1": {"variants": {"high": {}}}},
    }}}
    assert mounted.stat().st_mode & 0o077 == 0   # may hold a literal key


def test_opencode2_forwards_only_env_vars_named_by_global_providers(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"custom": {"options": {"apiKey": "{env:CUSTOM_KEY}"},
                                "env": ["CUSTOM_TOKEN", "UNSET_TOKEN"]}},
        "mcp": {"memory": {"headers": {"Authorization": "Bearer {env:MCP_TOKEN}"}}},
    }))

    args = auth_docker_args("opencode2", system="Linux", home=tmp_path, environ={
        "CUSTOM_KEY": "key-value", "CUSTOM_TOKEN": "token-value",
        "MCP_TOKEN": "mcp-value", "UNRELATED": "other-value",
    })

    env = [value for flag, value in zip(args, args[1:], strict=False) if flag == "-e"]
    assert env == ["OPENCODE_DISABLE_PROJECT_CONFIG=1", "CUSTOM_KEY", "CUSTOM_TOKEN"]
    assert not any("value" in arg for arg in args)   # values travel by name only


def test_opencode2_never_mounts_the_host_credential_store(tmp_path):
    # OpenCode 2 keeps logins in opencode.db and ignores auth.json in a fresh
    # container, so the old read-write auth.json mount delivered nothing.
    auth = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"openai": {"type": "api", "key": "sk-host"}}')

    assert auth_docker_args(
        "opencode2", system="Linux", home=tmp_path, environ={}, _require_exists=True,
    ) == ["-e", "OPENCODE_DISABLE_PROJECT_CONFIG=1"]


def test_opencode2_reads_the_global_config_under_xdg_config_home(tmp_path):
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    (xdg / "opencode" / "opencode.jsonc").write_text('{"provider": {"p": {}}}')

    args = auth_docker_args("opencode2", system="Linux", home=tmp_path,
                            environ={"XDG_CONFIG_HOME": str(xdg)})

    mounted = _mounted_ro(args, "/home/agent/.config/opencode/opencode.jsonc")
    assert json.loads(mounted.read_text()) == {"provider": {"p": {}}}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_opencode2_unreadable_config_dir_counts_as_no_config(tmp_path):
    config = _opencode_config(tmp_path, '{"provider": {"p": {"options": {"apiKey": "k"}}}}')
    config.parent.chmod(0)
    try:
        args = auth_docker_args("opencode2", system="Linux", home=tmp_path, environ={},
                                _require_exists=True)
        has_key = opencode2_has_provider_key(home=tmp_path, environ={})
    finally:
        config.parent.chmod(0o755)
    assert args == ["-e", "OPENCODE_DISABLE_PROJECT_CONFIG=1"]
    assert has_key is False


@pytest.mark.parametrize(("provider", "environ", "expected"), [
    ({"options": {"apiKey": "sk-literal"}}, {}, True),
    ({"options": {"apiKey": "{env:KEY}"}}, {"KEY": "v"}, True),
    ({"options": {"apiKey": "{env:KEY}"}}, {}, False),
    ({"env": ["KEY"]}, {"KEY": "v"}, True),
    ({"env": ["KEY"]}, {}, False),
    ({"options": {"apiKey": "{file:~/.secrets/key}"}}, {}, False),   # absent in the cage
    ({"models": {"m1": {}}}, {}, False),
])
def test_opencode2_provider_key_detection(tmp_path, provider, environ, expected):
    _opencode_config(tmp_path, json.dumps({"provider": {"p": provider}}))
    assert opencode2_has_provider_key(home=tmp_path, environ=environ) is expected


def test_opencode2_has_no_provider_key_without_a_global_config(tmp_path):
    assert opencode2_has_provider_key(home=tmp_path, environ={"OPENAI_API_KEY": "v"}) is False


def test_claude_linux_mounts_credentials_json_ro():
    args = auth_docker_args("claude", system="Linux", home=Path("/h"))
    assert "-v" in args
    assert "/h/.claude/.credentials.json:/home/agent/.claude/.credentials.json:ro" in args


def test_claude_macos_reads_keychain_into_env():
    def fake_run(cmd, **kw):
        assert "find-generic-password" in cmd

        class R:
            returncode = 0
            stdout = json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}})

        return R()
    args = auth_docker_args("claude", system="Darwin", run=fake_run, home=Path("/h"))
    assert "-e" in args and "CLAUDE_CODE_OAUTH_TOKEN=tok-123" in args


def test_missing_codex_login_raises_with_hint():
    with pytest.raises(AuthUnavailable):
        auth_docker_args(
            "codex", system="Linux", home=Path("/does/not/exist"), _require_exists=True,
        )


def test_claude_macos_keychain_miss_raises():
    # `security find-generic-password` exits non-zero when there is no
    # matching Keychain item (never ran `claude` on this Mac, or the item
    # was deleted) -- that's the macOS half of "unresolvable creds".
    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""

        return R()

    with pytest.raises(AuthUnavailable):
        auth_docker_args("claude", system="Darwin", run=fake_run, home=Path("/h"))


def test_claude_macos_non_json_stdout_raises():
    # `-w` succeeded (returncode 0) but the password field isn't the
    # expected JSON blob -- a corrupted/foreign Keychain item.
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "not-json"

        return R()

    with pytest.raises(AuthUnavailable):
        auth_docker_args("claude", system="Darwin", run=fake_run, home=Path("/h"))


def test_claude_macos_missing_access_token_raises():
    # Valid JSON, but missing the `claudeAiOauth.accessToken` path.
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = json.dumps({"claudeAiOauth": {}})

        return R()

    with pytest.raises(AuthUnavailable):
        auth_docker_args("claude", system="Darwin", run=fake_run, home=Path("/h"))


def test_unknown_harness_raises():
    with pytest.raises(ValueError, match="unknown harness"):
        auth_docker_args("pi", system="Linux", home=Path("/h"))


# --- broker path (§3d, INV2): auth_broker_args + broker_credential --------
#
# `auth_broker_args` returns env args that point a harness at the credential
# broker with a PLACEHOLDER key -- no mount, no real key. `broker_credential`
# is the host-side read the broker itself uses for its real `HeaderRewrite`
# (ContainerOperator wires the two together -- see test_container_operator.py).

from nethackers.harness.auth_inject import auth_broker_args, broker_credential  # noqa: E402


def test_claude_uses_broker_base_and_placeholder():
    args = auth_broker_args("claude", broker_base="http://host.docker.internal:5000")
    joined = " ".join(args)
    assert "ANTHROPIC_BASE_URL=http://host.docker.internal:5000" in joined
    assert "CLAUDE_CODE_OAUTH_TOKEN=proxy-managed" in joined
    assert "-v" not in args                       # no credential mount
    assert "REAL" not in joined                    # no real key crosses the boundary


def test_codex_uses_broker_base_writable_cage_no_token_no_config(tmp_path):
    # Codex broker path routes via a `-c` INVOCATION override + broker header
    # injection (build_docker_argv/_codex_cmd + broker_credential), NOT a cage
    # auth.json/config.toml (a ChatGPT-subscription login ignores OPENAI_BASE_URL,
    # and `codex exec --ignore-user-config` discards config.toml anyway). So
    # `auth_broker_args('codex')` returns ONLY a writable cage `~/.codex` dir +
    # CODEX_HOME (codex needs a writable $CODEX_HOME for its app-server socket/
    # state); the cage is EMPTY -- no token, no config crosses the boundary.
    #
    # A host ~/.codex login is present but MUST NOT be read/copied here: the
    # broker reads it host-side (broker_credential); the cage never sees it.
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "",
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "REAL-OAUTH-TOKEN",
            "refresh_token": "REAL-REFRESH-TOKEN",
            "account_id": "acct-xyz",
            "id_token": "REAL-ID-TOKEN",
        },
    }))
    broker_base = "http://host.docker.internal:5001"

    args = auth_broker_args("codex", broker_base=broker_base, home=tmp_path)

    # exactly a writable dir mount + CODEX_HOME env, no :ro, no OPENAI_BASE_URL
    cage_dir = tmp_path / ".nethackers" / "codex-cage"
    assert args == [
        "-v", f"{cage_dir}:/home/agent/.codex",
        "-e", "CODEX_HOME=/home/agent/.codex",
    ]
    joined = " ".join(args)
    assert "OPENAI_BASE_URL" not in joined
    assert ":ro" not in joined

    # the cage is a real, owner-only, EMPTY dir -- no token, no config file
    assert cage_dir.is_dir()
    assert cage_dir.stat().st_mode & 0o777 == 0o700
    assert not (cage_dir / "auth.json").exists()
    assert not (cage_dir / "config.toml").exists()
    assert list(cage_dir.iterdir()) == []

    # nothing from the host login (its real tokens) entered the cage or argv
    assert "REAL-OAUTH-TOKEN" not in joined
    assert "REAL-REFRESH-TOKEN" not in joined
    assert "REAL-ID-TOKEN" not in joined


def test_codex_cage_removes_stale_files_from_an_older_cage(tmp_path):
    # An older (placeholder-JWT + config.toml) cage left files behind; the new
    # empty-cage builder must clear them so the cage genuinely carries neither.
    cage_dir = tmp_path / ".nethackers" / "codex-cage"
    cage_dir.mkdir(parents=True)
    (cage_dir / "auth.json").write_text('{"stale": true}')
    (cage_dir / "config.toml").write_text('openai_base_url = "http://old:1"\n')

    auth_broker_args("codex", broker_base="http://x:1", home=tmp_path)

    assert not (cage_dir / "auth.json").exists()
    assert not (cage_dir / "config.toml").exists()


def test_codex_broker_args_requires_home(tmp_path):
    with pytest.raises(ValueError, match="home"):
        auth_broker_args("codex", broker_base="http://host.docker.internal:5001")


def test_codex_broker_args_needs_no_host_login(tmp_path):
    # The cage carries no token now (auth is broker-injected), so building it
    # never reads the host ~/.codex -- it just makes the empty writable cage,
    # even with no ~/.codex present. (Fail-loud on a missing login is
    # broker_credential's job -- see test_broker_credential_codex_missing_login_raises.)
    args = auth_broker_args("codex", broker_base="http://x:1", home=tmp_path)
    cage_dir = tmp_path / ".nethackers" / "codex-cage"
    assert args == [
        "-v", f"{cage_dir}:/home/agent/.codex",
        "-e", "CODEX_HOME=/home/agent/.codex",
    ]
    assert list(cage_dir.iterdir()) == []


def test_opencode2_broker_not_implemented():
    # `auth_broker_args` itself stays claude/codex-only: OpenCode's base-URL
    # override is a per-provider JSON field, not the single env var this
    # function's (harness, broker_base) shape assumes. The real opencode2
    # broker path -- when at least one provider is brokerable -- goes
    # through `opencode2_broker_targets`/`opencode2_broker_docker_args`
    # instead (see the section below); `ContainerOperator` never calls this
    # function for opencode2.
    with pytest.raises(NotImplementedError):
        auth_broker_args("opencode2", broker_base="http://host.docker.internal:5002")


def test_unknown_harness_broker_raises():
    with pytest.raises(ValueError, match="unknown harness"):
        auth_broker_args("pi", broker_base="http://host.docker.internal:5000")


def test_broker_credential_claude_linux_reads_credentials_json(tmp_path):
    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-linux"}})
    )
    rw = broker_credential("claude", system="Linux", home=tmp_path, environ={})
    assert rw.inject == (("Authorization", "Bearer tok-linux"),)
    assert rw.strip == ("x-api-key",)
    assert rw.merge_csv == (("anthropic-beta", ("oauth-2025-04-20",)),)


def test_broker_credential_claude_macos_reads_keychain():
    def fake_run(cmd, **kw):
        assert "find-generic-password" in cmd

        class R:
            returncode = 0
            stdout = json.dumps({"claudeAiOauth": {"accessToken": "tok-mac"}})

        return R()

    rw = broker_credential(
        "claude", system="Darwin", home=Path("/h"), run=fake_run, environ={},
    )
    assert rw.inject == (("Authorization", "Bearer tok-mac"),)
    assert rw.strip == ("x-api-key",)
    assert rw.merge_csv == (("anthropic-beta", ("oauth-2025-04-20",)),)


def test_broker_credential_claude_linux_missing_creds_raises(tmp_path):
    with pytest.raises(AuthUnavailable):
        broker_credential("claude", system="Linux", home=tmp_path, environ={})


def test_broker_credential_claude_macos_keychain_miss_raises():
    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""

        return R()

    with pytest.raises(AuthUnavailable):
        broker_credential("claude", system="Darwin", home=Path("/h"), run=fake_run, environ={})


def test_broker_credential_claude_setup_token_env_wins(tmp_path):
    # A durable setup-token via env short-circuits before any keychain/file
    # read -- no .credentials.json exists under tmp_path, so a successful
    # return here already proves the env path was taken.
    rw = broker_credential(
        "claude", system="Linux", home=tmp_path,
        environ={"NETHACKERS_CLAUDE_SETUP_TOKEN": "ENVTOK"},
    )
    assert rw.inject == (("Authorization", "Bearer ENVTOK"),)
    assert rw.strip == ("x-api-key",)
    assert rw.merge_csv == (("anthropic-beta", ("oauth-2025-04-20",)),)


def test_broker_credential_claude_setup_token_file_used_when_env_unset(tmp_path):
    token_dir = tmp_path / ".nethackers" / "claude"
    token_dir.mkdir(parents=True)
    (token_dir / "setup-token").write_text("FILETOK\n")
    rw = broker_credential("claude", system="Linux", home=tmp_path, environ={})
    assert rw.inject == (("Authorization", "Bearer FILETOK"),)
    assert rw.strip == ("x-api-key",)
    assert rw.merge_csv == (("anthropic-beta", ("oauth-2025-04-20",)),)


def test_broker_credential_claude_falls_back_to_login_token_and_warns(tmp_path, caplog):
    # No setup-token anywhere (env unset, no ~/.nethackers/claude/setup-token)
    # -- falls back to the ~8h login token and must warn, per INV B4 (the
    # fallback is used as-is, never auto-refreshed).
    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-login-fallback"}})
    )
    with caplog.at_level(logging.WARNING, logger="nethackers.harness.auth_inject"):
        rw = broker_credential("claude", system="Linux", home=tmp_path, environ={})
    assert rw.inject == (("Authorization", "Bearer tok-login-fallback"),)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("setup-token" in msg for msg in warnings)


def _write_codex_chatgpt_login(home: Path, *, access_token, account_id="acct-123") -> None:
    """A ChatGPT-subscription `~/.codex/auth.json` (empty OPENAI_API_KEY,
    rotating OAuth `tokens`) for the broker_credential codex tests."""
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    tokens = {"access_token": access_token, "refresh_token": "old-refresh"}
    if account_id is not None:
        tokens["account_id"] = account_id
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "", "auth_mode": "chatgpt", "tokens": tokens,
    }))


def test_broker_credential_codex_fresh_token_injects_bearer_and_account_id(tmp_path, monkeypatch):
    # A still-valid (far-exp) JWT: inject it as-is + the account-id header,
    # and DO NOT refresh (a refresh here would be a bug).
    fresh = _make_jwt(int(time.time()) + 86400)
    _write_codex_chatgpt_login(tmp_path, access_token=fresh, account_id="acct-123")

    def _no_refresh(home):
        raise AssertionError("refresh must not run for a fresh token")
    monkeypatch.setattr(auth_inject, "_codex_refresh", _no_refresh)

    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert rw.inject == (
        ("Authorization", f"Bearer {fresh}"),
        ("ChatGPT-Account-Id", "acct-123"),
    )


def test_broker_credential_codex_near_expiry_refreshes(tmp_path, monkeypatch):
    near = _make_jwt(int(time.time()) + 60)   # ~1 min left -- inside the margin
    _write_codex_chatgpt_login(tmp_path, access_token=near, account_id="acct-7")
    monkeypatch.setattr(auth_inject, "_codex_refresh", lambda home: "FRESH-ACCESS")

    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert rw.inject == (
        ("Authorization", "Bearer FRESH-ACCESS"),
        ("ChatGPT-Account-Id", "acct-7"),
    )


def test_broker_credential_codex_undecodable_token_refreshes(tmp_path, monkeypatch):
    _write_codex_chatgpt_login(tmp_path, access_token="not-a-jwt", account_id="acct-9")
    monkeypatch.setattr(auth_inject, "_codex_refresh", lambda home: "REFRESHED")

    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert ("Authorization", "Bearer REFRESHED") in rw.inject


def test_broker_credential_codex_missing_account_id_omits_header(tmp_path, monkeypatch):
    fresh = _make_jwt(int(time.time()) + 86400)
    _write_codex_chatgpt_login(tmp_path, access_token=fresh, account_id=None)

    def _no_refresh(home):
        raise AssertionError("refresh must not run for a fresh token")
    monkeypatch.setattr(auth_inject, "_codex_refresh", _no_refresh)

    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert rw.inject == (("Authorization", f"Bearer {fresh}"),)
    assert not any(name == "ChatGPT-Account-Id" for name, _ in rw.inject)


def test_broker_credential_codex_missing_login_raises(tmp_path):
    with pytest.raises(AuthUnavailable):
        broker_credential("codex", system="Linux", home=tmp_path)


def test_broker_credential_unknown_harness_raises():
    with pytest.raises(ValueError):
        broker_credential("opencode2", system="Linux", home=Path("/h"))


# --- opencode2 broker path (§3d, INV2): per-provider ------------------------
#
# opencode2 is multi-provider (its base-URL override is a per-provider JSON
# field, not a single env var), so `auth_broker_args` stays claude/codex-only
# (see test_opencode2_broker_not_implemented above) and gets its own pair of
# functions instead: `opencode2_broker_targets` resolves, per provider across
# the global configs, whether it's brokerable and how (upstream/header);
# `opencode2_broker_docker_args` writes the cage config that actually points
# the brokered ones at their brokers (`ContainerOperator` wires the two
# together -- see test_container_operator.py).

from nethackers.harness.auth_inject import (  # noqa: E402
    opencode2_broker_docker_args,
    opencode2_broker_targets,
)


def test_opencode2_broker_targets_anthropic_default_uses_x_api_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"anthropic": {"options": {"apiKey": "sk-ant-real"}}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert targets == [{
        "file": "opencode.json", "name": "anthropic",
        "upstream": "https://api.anthropic.com",
        "rewrite": HeaderRewrite(inject=(("x-api-key", "sk-ant-real"),)),
    }]


def test_opencode2_broker_targets_openai_default_uses_bearer(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"openai": {"options": {"apiKey": "sk-oa-real"}}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert targets == [{
        "file": "opencode.json", "name": "openai",
        "upstream": "https://api.openai.com/v1",
        "rewrite": HeaderRewrite(inject=(("Authorization", "Bearer sk-oa-real"),)),
    }]


def test_opencode2_broker_targets_explicit_base_url_uses_bearer_by_default(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"custom": {"options": {
            "apiKey": "sk-custom", "baseURL": "https://api.custom.example/v1",
        }}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert targets == [{
        "file": "opencode.json", "name": "custom",
        "upstream": "https://api.custom.example/v1",
        "rewrite": HeaderRewrite(inject=(("Authorization", "Bearer sk-custom"),)),
    }]


def test_opencode2_broker_targets_anthropic_named_provider_keeps_x_api_key_with_base_url(tmp_path):
    # Name-based rule wins first: a provider literally named "anthropic"
    # gets x-api-key even carrying its own (e.g. mirror/proxy) baseURL.
    _opencode_config(tmp_path, json.dumps({
        "provider": {"anthropic": {"options": {
            "apiKey": "sk-ant", "baseURL": "https://mirror.example/anthropic",
        }}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert targets == [{
        "file": "opencode.json", "name": "anthropic",
        "upstream": "https://mirror.example/anthropic",
        "rewrite": HeaderRewrite(inject=(("x-api-key", "sk-ant"),)),
    }]


def test_opencode2_broker_targets_base_url_host_ending_anthropic_com_uses_x_api_key(tmp_path):
    # Host-based rule: a DIFFERENTLY-named provider pointed at an
    # Anthropic-shaped host also gets x-api-key.
    _opencode_config(tmp_path, json.dumps({
        "provider": {"my-claude": {"options": {
            "apiKey": "sk-ant2", "baseURL": "https://eu.anthropic.com",
        }}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert targets == [{
        "file": "opencode.json", "name": "my-claude",
        "upstream": "https://eu.anthropic.com",
        "rewrite": HeaderRewrite(inject=(("x-api-key", "sk-ant2"),)),
    }]


def test_opencode2_broker_targets_resolves_env_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"openai": {"options": {"apiKey": "{env:MY_OPENAI_KEY}"}}},
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={"MY_OPENAI_KEY": "sk-from-env"})
    assert targets == [{
        "file": "opencode.json", "name": "openai",
        "upstream": "https://api.openai.com/v1",
        "rewrite": HeaderRewrite(inject=(("Authorization", "Bearer sk-from-env"),)),
    }]


def test_opencode2_broker_targets_skips_unset_env_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"openai": {"options": {"apiKey": "{env:MY_OPENAI_KEY}"}}},
    }))
    assert opencode2_broker_targets(home=tmp_path, environ={}) == []


def test_opencode2_broker_targets_skips_file_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"anthropic": {"options": {"apiKey": "{file:~/.secrets/key}"}}},
    }))
    assert opencode2_broker_targets(home=tmp_path, environ={}) == []


def test_opencode2_broker_targets_skips_provider_with_no_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"anthropic": {"models": {"m1": {}}}},
    }))
    assert opencode2_broker_targets(home=tmp_path, environ={}) == []


def test_opencode2_broker_targets_skips_unknown_provider_without_base_url(tmp_path):
    # A perfectly good key, but no baseURL and not a name this module knows a
    # default host for -- nowhere to broker it TO.
    _opencode_config(tmp_path, json.dumps({
        "provider": {"mystery": {"options": {"apiKey": "sk-mystery"}}},
    }))
    assert opencode2_broker_targets(home=tmp_path, environ={}) == []


def test_opencode2_broker_targets_mixed_providers_only_lists_the_brokerable_ones(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {
            "anthropic": {"options": {"apiKey": "sk-ant"}},
            "mystery": {"options": {"apiKey": "sk-mystery"}},
            "custom": {"options": {"apiKey": "{file:~/.secrets/k}"}},
        },
    }))
    targets = opencode2_broker_targets(home=tmp_path, environ={})
    assert [t["name"] for t in targets] == ["anthropic"]


def test_opencode2_broker_targets_no_global_config_is_empty(tmp_path):
    assert opencode2_broker_targets(home=tmp_path, environ={}) == []


def test_opencode2_broker_docker_args_rewrites_brokered_provider_and_keeps_others(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {
            "anthropic": {"options": {"apiKey": "sk-ant-real"},
                          "models": {"opus": {"variants": {"high": {}}}}},
            "custom": {"options": {"apiKey": "{env:CUSTOM_KEY}"}},
        },
    }))
    broker_bases = {("opencode.json", "anthropic"): "http://host.docker.internal:9001"}

    args = opencode2_broker_docker_args(
        tmp_path, environ={"CUSTOM_KEY": "custom-value"}, broker_bases=broker_bases,
    )

    mounted = _mounted_ro(args, "/home/agent/.config/opencode/opencode.json")
    doc = json.loads(mounted.read_text())
    assert doc["provider"]["anthropic"]["options"] == {
        "baseURL": "http://host.docker.internal:9001", "apiKey": "proxy-managed",
    }
    assert doc["provider"]["anthropic"]["models"] == {"opus": {"variants": {"high": {}}}}
    assert doc["provider"]["custom"] == {"options": {"apiKey": "{env:CUSTOM_KEY}"}}
    # only the non-brokered provider's env var is forwarded
    env = [value for flag, value in zip(args, args[1:], strict=False) if flag == "-e"]
    assert env == ["OPENCODE_DISABLE_PROJECT_CONFIG=1", "CUSTOM_KEY"]
    assert not any("sk-ant-real" in arg or "custom-value" in arg for arg in args)


def test_opencode2_broker_docker_args_does_not_forward_a_brokered_providers_env_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"openai": {"options": {"apiKey": "{env:OPENAI_REAL_KEY}"}}},
    }))
    broker_bases = {("opencode.json", "openai"): "http://host.docker.internal:9002"}

    args = opencode2_broker_docker_args(
        tmp_path, environ={"OPENAI_REAL_KEY": "sk-should-not-forward"}, broker_bases=broker_bases,
    )

    env = [value for flag, value in zip(args, args[1:], strict=False) if flag == "-e"]
    assert env == ["OPENCODE_DISABLE_PROJECT_CONFIG=1"]   # brokered -- nothing else forwarded
    mounted = _mounted_ro(args, "/home/agent/.config/opencode/opencode.json")
    doc = json.loads(mounted.read_text())
    assert doc["provider"]["openai"]["options"]["apiKey"] == "proxy-managed"
    assert not any("sk-should-not-forward" in arg for arg in args)


def test_opencode2_broker_docker_args_with_no_brokered_providers_matches_the_mount_path(tmp_path):
    # Same config/env as test_opencode2_forwards_only_env_vars_named_by_global_providers
    # above -- with nothing in broker_bases, this must forward exactly what
    # the plain mount path (_opencode2_docker_args, via auth_docker_args)
    # would: both CUSTOM_KEY (referenced inside apiKey) and CUSTOM_TOKEN
    # (the provider's own `env` list).
    _opencode_config(tmp_path, json.dumps({
        "provider": {"custom": {"options": {"apiKey": "{env:CUSTOM_KEY}"},
                                "env": ["CUSTOM_TOKEN"]}},
    }))
    args = opencode2_broker_docker_args(
        tmp_path, environ={"CUSTOM_KEY": "v", "CUSTOM_TOKEN": "t"}, broker_bases={},
    )
    env = [value for flag, value in zip(args, args[1:], strict=False) if flag == "-e"]
    assert env == ["OPENCODE_DISABLE_PROJECT_CONFIG=1", "CUSTOM_KEY", "CUSTOM_TOKEN"]


def test_opencode2_broker_docker_args_mounts_owner_only_even_with_a_literal_key(tmp_path):
    _opencode_config(tmp_path, json.dumps({
        "provider": {"anthropic": {"options": {"apiKey": "sk-ant-real"}}},
    }))
    broker_bases = {("opencode.json", "anthropic"): "http://host.docker.internal:9001"}
    args = opencode2_broker_docker_args(tmp_path, environ={}, broker_bases=broker_bases)
    mounted = _mounted_ro(args, "/home/agent/.config/opencode/opencode.json")
    assert mounted.stat().st_mode & 0o077 == 0


# --- Codex OAuth refresh (§3.3, Task 4a): _codex_creds + _codex_refresh ----
#
# `_codex_creds` is the parsed `~/.codex/auth.json`, raising `AuthUnavailable`
# on a missing/unreadable/malformed file. `_codex_refresh` does the broker's
# proactive/reactive OAuth refresh (§3.3): POST `grant_type=refresh_token` to
# auth.openai.com, then WRITE BACK the rotated access_token + refresh_token to
# the canonical file (the refresh token is single-use, so skipping the
# write-back bricks the host's own `codex login`). Not yet wired into
# `broker_credential` -- that's Task 4b.

from nethackers.harness.auth_inject import (  # noqa: E402
    CODEX_OAUTH_CLIENT_ID,
    _codex_creds,
    _codex_refresh,
    _codex_token_needs_refresh,
)

_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


def _codex_auth_doc(**overrides) -> dict:
    doc = {
        "OPENAI_API_KEY": None,
        "auth_mode": "chatgpt",
        "last_refresh": "2026-09-01T00:00:00Z",
        "tokens": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "id_token": "old-id-token",
            "account_id": "acct-123",
        },
    }
    doc.update(overrides)
    return doc


def _write_codex_auth(home: Path, doc: dict) -> Path:
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    path = codex_dir / "auth.json"
    path.write_text(json.dumps(doc))
    return path


class _FakeTokenResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> dict:
        return self._payload


def _fake_post(status_code: int, payload: dict | None = None, *, calls: list | None = None):
    """A ``post(url, *, data)``-shaped fake standing in for ``httpx.post``,
    never touching the network. Records every call into ``calls`` when given
    one, so a test can assert the exact OAuth request sent."""

    def post(url, *, data):
        if calls is not None:
            calls.append({"url": url, "data": data})
        return _FakeTokenResponse(status_code, payload)

    return post


def test_codex_creds_returns_parsed_auth_json(tmp_path):
    doc = _codex_auth_doc()
    _write_codex_auth(tmp_path, doc)
    assert _codex_creds(tmp_path) == doc


def test_codex_creds_missing_file_raises(tmp_path):
    with pytest.raises(AuthUnavailable):
        _codex_creds(tmp_path)


def test_codex_creds_malformed_json_raises(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text("not json")
    with pytest.raises(AuthUnavailable):
        _codex_creds(tmp_path)


def test_codex_creds_non_dict_json_raises(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text("[]")
    with pytest.raises(AuthUnavailable):
        _codex_creds(tmp_path)


def test_codex_refresh_writes_back_new_tokens_and_returns_access_token(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"})

    result = _codex_refresh(tmp_path, post=post)

    assert result == "new-acc"
    doc = json.loads(path.read_text())
    assert doc["tokens"]["access_token"] == "new-acc"
    assert doc["tokens"]["refresh_token"] == "new-ref"
    assert doc["auth_mode"] == "chatgpt"
    assert doc["tokens"]["account_id"] == "acct-123"
    assert doc["tokens"]["id_token"] == "old-id-token"
    assert doc["last_refresh"] == "2026-09-01T00:00:00Z"
    assert path.stat().st_mode & 0o777 == 0o600


def test_codex_refresh_posts_the_correct_oauth_request(tmp_path):
    _write_codex_auth(tmp_path, _codex_auth_doc())
    calls: list = []
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"}, calls=calls)

    _codex_refresh(tmp_path, post=post)

    assert calls == [{
        "url": _CODEX_TOKEN_URL,
        "data": {
            "grant_type": "refresh_token",
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "refresh_token": "old-refresh",
        },
    }]


def test_codex_refresh_is_single_use_old_refresh_token_gone(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"})

    _codex_refresh(tmp_path, post=post)

    raw = path.read_text()
    assert "old-refresh" not in raw
    assert json.loads(raw)["tokens"]["refresh_token"] == "new-ref"


def test_codex_refresh_keeps_old_refresh_token_when_endpoint_omits_one(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    post = _fake_post(200, {"access_token": "new-acc"})  # no refresh_token in the response

    result = _codex_refresh(tmp_path, post=post)

    assert result == "new-acc"
    assert json.loads(path.read_text())["tokens"]["refresh_token"] == "old-refresh"


def test_codex_refresh_updates_id_token_when_endpoint_returns_one(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    post = _fake_post(200, {
        "access_token": "new-acc", "refresh_token": "new-ref", "id_token": "new-id-token",
    })

    _codex_refresh(tmp_path, post=post)

    assert json.loads(path.read_text())["tokens"]["id_token"] == "new-id-token"


def test_codex_refresh_non_200_raises_and_leaves_file_unchanged(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()
    post = _fake_post(401, {"error": "invalid_grant"})

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert path.read_bytes() == before


def test_codex_refresh_missing_refresh_token_raises(tmp_path):
    doc = _codex_auth_doc()
    doc["tokens"].pop("refresh_token")
    path = _write_codex_auth(tmp_path, doc)
    before = path.read_bytes()
    calls: list = []
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"}, calls=calls)

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert calls == []  # never even attempts the network call
    assert path.read_bytes() == before


def test_codex_refresh_malformed_auth_file_raises(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text("not json")
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"})

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)


def test_codex_refresh_no_access_token_in_response_raises_and_leaves_file_unchanged(tmp_path):
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()
    post = _fake_post(200, {"refresh_token": "new-ref"})  # no access_token

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert path.read_bytes() == before


# --- Codex proactive-refresh margin: _codex_token_needs_refresh (Task 4b) ---
#
# The broker refreshes the rotating access token by its JWT `exp` rather than
# pinning one ~8h snapshot. "Refresh when uncertain" is the safe default: a
# token that can't be decoded as a JWT with a numeric `exp` is treated as one
# that may already be stale.

def test_codex_token_needs_refresh_far_future_is_false():
    assert _codex_token_needs_refresh(_make_jwt(int(time.time()) + 86400)) is False


def test_codex_token_needs_refresh_near_expiry_is_true():
    assert _codex_token_needs_refresh(_make_jwt(int(time.time()) + 60)) is True


def test_codex_token_needs_refresh_expired_is_true():
    assert _codex_token_needs_refresh(_make_jwt(int(time.time()) - 10)) is True


def test_codex_token_needs_refresh_non_jwt_is_true():
    assert _codex_token_needs_refresh("garbage") is True
    assert _codex_token_needs_refresh("a.b.c") is True       # middle segment not base64 JSON
    assert _codex_token_needs_refresh("") is True


def test_codex_token_needs_refresh_jwt_without_exp_is_true():
    header = _b64url_json({"alg": "none"})
    payload = _b64url_json({"sub": "x"})   # no exp claim
    assert _codex_token_needs_refresh(f"{header}.{payload}.") is True


def test_codex_token_needs_refresh_non_numeric_exp_is_true():
    header = _b64url_json({"alg": "none"})
    payload = _b64url_json({"exp": "soon"})   # exp not a number
    assert _codex_token_needs_refresh(f"{header}.{payload}.") is True


# --- Task 4b: four parked Task-4a review findings folded into _codex_refresh -

def test_codex_refresh_non_string_refresh_token_raises_cleanly(tmp_path):
    # Finding (a): a malformed (non-string) refresh_token must raise a clean
    # AuthUnavailable BEFORE any network call (same shape as the access_token
    # isinstance guards) -- not a raw httpx error.
    doc = _codex_auth_doc()
    doc["tokens"]["refresh_token"] = 12345
    path = _write_codex_auth(tmp_path, doc)
    before = path.read_bytes()
    calls: list = []
    post = _fake_post(200, {"access_token": "x", "refresh_token": "y"}, calls=calls)

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert calls == []                       # never even attempts the network call
    assert path.read_bytes() == before


def test_codex_refresh_httpx_error_becomes_auth_unavailable(tmp_path):
    # Finding (b): a connect/timeout/transport failure surfaces as
    # AuthUnavailable, not a raw httpx exception (a live run calls this).
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()

    def post(url, *, data):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert path.read_bytes() == before


def test_codex_refresh_unreadable_json_becomes_auth_unavailable(tmp_path):
    # Finding (b): a 200 whose .json() raises becomes AuthUnavailable.
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()

    class _Raises:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=lambda url, *, data: _Raises())

    assert path.read_bytes() == before


def test_codex_refresh_non_dict_json_becomes_auth_unavailable(tmp_path):
    # Finding (b): a 200 whose .json() is not a dict becomes AuthUnavailable
    # (never an AttributeError on `.get`).
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()

    class _ListJson:
        status_code = 200

        def json(self):
            return ["not", "a", "dict"]

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=lambda url, *, data: _ListJson())

    assert path.read_bytes() == before


def test_codex_refresh_write_failure_raises_and_leaves_file_unchanged(tmp_path, monkeypatch):
    # Finding (c): if the atomic write-back fails, raise AuthUnavailable and
    # leave the canonical file byte-for-byte unchanged (INV B4).
    path = _write_codex_auth(tmp_path, _codex_auth_doc())
    before = path.read_bytes()
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"})

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(AuthUnavailable):
        _codex_refresh(tmp_path, post=post)

    assert path.read_bytes() == before


def test_codex_refresh_preserves_the_openai_api_key_field(tmp_path):
    # Finding (d): OPENAI_API_KEY is carried across a refresh by name (a
    # chatgpt-mode login keeps it as an empty string).
    doc = _codex_auth_doc()
    doc["OPENAI_API_KEY"] = ""
    path = _write_codex_auth(tmp_path, doc)
    post = _fake_post(200, {"access_token": "new-acc", "refresh_token": "new-ref"})

    _codex_refresh(tmp_path, post=post)

    reloaded = json.loads(path.read_text())
    assert "OPENAI_API_KEY" in reloaded
    assert reloaded["OPENAI_API_KEY"] == ""
