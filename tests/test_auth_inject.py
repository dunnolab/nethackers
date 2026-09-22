import json
import logging
import os
from pathlib import Path

import pytest

from nethackers.harness.auth_inject import (
    AuthUnavailable,
    auth_docker_args,
    opencode2_has_provider_key,
)
from nethackers.harness.cred_broker import HeaderRewrite


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


def test_codex_uses_broker_base_and_placeholder():
    args = auth_broker_args("codex", broker_base="http://host.docker.internal:5001")
    joined = " ".join(args)
    assert "OPENAI_BASE_URL=http://host.docker.internal:5001" in joined
    assert "OPENAI_API_KEY=proxy-managed" in joined
    assert "-v" not in args
    assert "REAL" not in joined


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
        broker_credential("claude", system="Linux", home=tmp_path)


def test_broker_credential_claude_macos_keychain_miss_raises():
    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""

        return R()

    with pytest.raises(AuthUnavailable):
        broker_credential("claude", system="Darwin", home=Path("/h"), run=fake_run)


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


def test_broker_credential_codex_prefers_stable_api_key(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "sk-real",
        "tokens": {"access_token": "should-not-be-used"},
    }))
    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert rw.inject == (("Authorization", "Bearer sk-real"),)


def test_broker_credential_codex_falls_back_to_oauth_access_token(tmp_path):
    # No stable API-key login -- only a ChatGPT/OAuth session. This snapshots
    # the rotating access_token once; see the module docstring's Broker
    # section for the staleness caveat this creates on a long mutator run.
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": None,
        "tokens": {"access_token": "chatgpt-oauth-tok"},
    }))
    rw = broker_credential("codex", system="Linux", home=tmp_path)
    assert rw.inject == (("Authorization", "Bearer chatgpt-oauth-tok"),)


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
