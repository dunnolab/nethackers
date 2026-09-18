import json
import os
from pathlib import Path

import pytest

from nethackers.harness.auth_inject import (
    AuthUnavailable,
    auth_docker_args,
    opencode2_has_provider_key,
)


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
# is the host-side read the broker itself uses for its real `header_value`
# (ContainerOperator wires the two together -- see test_container_operator.py).

from nethackers.harness.auth_inject import auth_broker_args, broker_credential  # noqa: E402


def test_claude_uses_broker_base_and_placeholder():
    args = auth_broker_args("claude", broker_base="http://host.docker.internal:5000")
    joined = " ".join(args)
    assert "ANTHROPIC_BASE_URL=http://host.docker.internal:5000" in joined
    assert "ANTHROPIC_API_KEY=proxy-managed" in joined
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
    # OpenCode's base-URL override is a per-provider JSON field, not a single
    # env var -- not guessed; callers keep using the credential mount.
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
    header_name, header_value = broker_credential("claude", system="Linux", home=tmp_path)
    assert header_name == "x-api-key"
    assert header_value == "tok-linux"


def test_broker_credential_claude_macos_reads_keychain():
    def fake_run(cmd, **kw):
        assert "find-generic-password" in cmd

        class R:
            returncode = 0
            stdout = json.dumps({"claudeAiOauth": {"accessToken": "tok-mac"}})

        return R()

    header_name, header_value = broker_credential(
        "claude", system="Darwin", home=Path("/h"), run=fake_run,
    )
    assert header_name == "x-api-key"
    assert header_value == "tok-mac"


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


def test_broker_credential_codex_prefers_stable_api_key(tmp_path):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "sk-real",
        "tokens": {"access_token": "should-not-be-used"},
    }))
    header_name, header_value = broker_credential("codex", system="Linux", home=tmp_path)
    assert header_name == "Authorization"
    assert header_value == "Bearer sk-real"


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
    header_name, header_value = broker_credential("codex", system="Linux", home=tmp_path)
    assert header_name == "Authorization"
    assert header_value == "Bearer chatgpt-oauth-tok"


def test_broker_credential_codex_missing_login_raises(tmp_path):
    with pytest.raises(AuthUnavailable):
        broker_credential("codex", system="Linux", home=tmp_path)


def test_broker_credential_unknown_harness_raises():
    with pytest.raises(ValueError):
        broker_credential("opencode2", system="Linux", home=Path("/h"))
