import json
from pathlib import Path

import pytest

from nethackers.harness.auth_inject import AuthUnavailable, auth_docker_args


def test_codex_mounts_the_one_canonical_dir():
    args = auth_docker_args("codex", system="Linux", home=Path("/h"))
    assert args == ["-v", "/h/.codex:/home/agent/.codex"]


def test_opencode2_mounts_global_config_ro_and_optional_auth_rw(tmp_path):
    config = tmp_path / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    auth = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}")

    args = auth_docker_args("opencode2", system="Linux", home=tmp_path)
    assert args == [
        "-v",
        f"{config}:/home/agent/.config/opencode/opencode.json:ro",
        "-v",
        f"{auth}:/home/agent/.local/share/opencode/auth.json",
    ]
    assert not args[-1].endswith(":ro")


def test_opencode2_config_does_not_require_auth_login(tmp_path):
    config = tmp_path / ".config" / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    assert auth_docker_args(
        "opencode2", system="Linux", home=tmp_path, _require_exists=True,
    ) == ["-v", f"{config}:/home/agent/.config/opencode/opencode.jsonc:ro"]


def test_opencode2_forwards_only_env_vars_referenced_by_global_or_project_config(tmp_path):
    global_config = tmp_path / ".config" / "opencode" / "opencode.json"
    global_config.parent.mkdir(parents=True)
    global_config.write_text('{"apiKey": "{env:CUSTOM_API_KEY}"}')
    project = tmp_path / "project"
    project.mkdir()
    (project / "opencode.json").write_text(
        '{"baseURL": "{env:CUSTOM_BASE_URL}", "env": ["CUSTOM_PROVIDER_TOKEN"]}'
    )

    args = auth_docker_args(
        "opencode2", system="Linux", home=tmp_path, project=project,
        environ={"CUSTOM_API_KEY": "secret", "CUSTOM_BASE_URL": "https://example.test",
                 "CUSTOM_PROVIDER_TOKEN": "token",
                 "UNRELATED_SECRET": "do-not-forward"},
    )
    assert args[2:4] == ["-e", "CUSTOM_API_KEY"]
    assert args[4:6] == ["-e", "CUSTOM_BASE_URL"]
    assert args[6:8] == ["-e", "CUSTOM_PROVIDER_TOKEN"]
    assert "UNRELATED_SECRET" not in args
    assert "secret" not in args


def test_opencode2_without_config_or_auth_needs_no_mount(tmp_path):
    assert auth_docker_args(
        "opencode2", system="Linux", home=tmp_path, _require_exists=True,
    ) == []


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
