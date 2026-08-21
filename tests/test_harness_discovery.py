import json
from pathlib import Path
from types import SimpleNamespace

from nethackers.harness.discovery import (
    CliInfo,
    ModelInfo,
    detect_cli,
    is_model_available,
    list_models,
    preflight_model,
)

# `supported_reasoning_levels` is a list of {"effort", "description"} DICTS in the
# real `codex debug models` output (NOT bare strings) -- the fixture mirrors that
# so the parser is exercised against the real shape.
_CODEX_JSON = json.dumps({"models": [
    {"slug": "gpt-5.6-sol", "display_name": "gpt-5.6-sol", "visibility": "list",
     "supported_reasoning_levels": [{"effort": "low", "description": "Fast"},
                                    {"effort": "high", "description": "Deep"}], "upgrade": None},
    {"slug": "gpt-5.4", "display_name": "gpt-5.4",
     "visibility": "list", "supported_reasoning_levels": [], "upgrade": "gpt-5.6-terra"},
    {"slug": "codex-auto-review", "display_name": "auto", "visibility": "hide"},
]})


def _run_ok(stdout):
    return lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout)


def test_list_models_codex_parses_debug_models_and_drops_hidden():
    models = list_models("codex", run=_run_ok(_CODEX_JSON))
    ids = [m.id for m in models]
    assert ids == ["gpt-5.6-sol", "gpt-5.4"]            # codex-auto-review (hide) dropped
    assert models[0].reasoning == ("low", "high")
    assert models[1].deprecated is True                  # non-null "upgrade"


def test_list_models_codex_falls_back_to_cache_file(tmp_path):
    cache = tmp_path / ".codex" / "models_cache.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(_CODEX_JSON)
    def _run_fail(*a, **k):
        return SimpleNamespace(returncode=1, stdout="")   # old CLI: no `debug models`
    models = list_models("codex", run=_run_fail, home=tmp_path)
    assert [m.id for m in models] == ["gpt-5.6-sol", "gpt-5.4"]


def test_list_models_codex_returns_none_when_nothing_available(tmp_path):
    def _run_fail(*a, **k):
        return SimpleNamespace(returncode=1, stdout="")
    assert list_models("codex", run=_run_fail, home=tmp_path) is None   # no cache file either


# capabilities.effort = {"supported", "<level>": {"supported"}} in the real
# /v1/models output -> reasoning is the supported effort levels, low..max order.
_ANTHROPIC_OK = {"data": [
    {"id": "claude-opus-5", "display_name": "Opus 5",
     "capabilities": {"effort": {"supported": True, "low": {"supported": True},
                                 "high": {"supported": True}, "max": {"supported": False}}}},
    {"id": "claude-fable-5", "display_name": "Fable 5"},   # no capabilities -> reasoning ()
], "has_more": False}


def _http(status, payload):
    def get(url, **kwargs):
        return SimpleNamespace(status_code=status, json=lambda: payload)
    return get


def test_list_models_claude_from_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")   # 3rd-tier credential
    models = list_models(
        "claude",
        run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),   # keychain miss -> env tier
        http=_http(200, _ANTHROPIC_OK),
        home=tmp_path,
    )
    assert [m.id for m in models] == ["claude-opus-5", "claude-fable-5"]
    assert models[0].label == "Opus 5"
    assert models[0].reasoning == ("low", "high")   # supported efforts (max: supported=False)
    assert models[1].reasoning == ()                 # no capabilities -> empty


def test_list_models_claude_401_is_unknown_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stale")
    # 401 = stale token, NOT "no models" -> None (warn+proceed), never []
    models = list_models(
        "claude",
        run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
        http=_http(401, {}),
        home=tmp_path,
    )
    assert models is None


def test_list_models_claude_no_credential_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called = {"n": 0}
    def _http_never(*a, **k):
        called["n"] += 1
        raise AssertionError("must not hit the network without a credential")
    # no keychain (fake run fails), no creds file, no env -> None, no request made
    def _run_fail(*a, **k):
        return SimpleNamespace(returncode=1, stdout="")
    assert list_models("claude", run=_run_fail, http=_http_never, home=tmp_path) is None
    assert called["n"] == 0


def test_claude_credential_reads_linux_creds_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds = tmp_path / ".claude" / ".credentials.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "oauth-xyz"}}))
    seen = {}
    def get(url, **kwargs):
        seen["auth"] = kwargs["headers"].get("Authorization")
        seen["beta"] = kwargs["headers"].get("anthropic-beta")
        return SimpleNamespace(status_code=200, json=lambda: _ANTHROPIC_OK)
    def _run_fail(*a, **k):   # force past the keychain tier
        return SimpleNamespace(returncode=1, stdout="")
    models = list_models("claude", run=_run_fail, http=get, home=tmp_path)
    assert [m.id for m in models] == ["claude-opus-5", "claude-fable-5"]
    assert seen["auth"] == "Bearer oauth-xyz"     # token used, never logged
    assert seen["beta"] == "oauth-2025-04-20"     # Bearer path needs the OAuth beta header


_CODEX_MODELS = [ModelInfo("gpt-5.6-sol", "gpt-5.6-sol")]
_CLAUDE_MODELS = [ModelInfo("claude-opus-5", "Opus 5")]


def test_is_model_available_codex_membership():
    assert is_model_available("codex", "gpt-5.6-sol", models=_CODEX_MODELS) is True
    assert is_model_available("codex", "gpt-9.9-nope", models=_CODEX_MODELS) is False


def test_is_model_available_claude_alias_always_true():
    # aliases resolve server-side; never block them even with an empty list
    assert is_model_available("claude", "opus", models=[]) is True
    assert is_model_available("claude", "fable", models=None, run=lambda *a, **k: None) is True


def test_is_model_available_strips_1m_suffix():
    assert is_model_available("claude", "claude-opus-5[1m]", models=_CLAUDE_MODELS) is True


def test_is_model_available_none_list_is_unknown(tmp_path):
    # models is None (discovery failed) and not an alias -> None (unknown)
    assert is_model_available(
        "claude", "claude-x", models=None,
        run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
        home=tmp_path,
        http=lambda *a, **k: (_ for _ in ()).throw(RuntimeError),
    ) is None


def test_detect_cli_installed_reports_version():
    def which(_b): return "/usr/local/bin/codex"
    def run(cmd, **k):
        from types import SimpleNamespace
        if cmd[:2] == ["codex", "--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.146.0\n")
        return SimpleNamespace(returncode=0, stdout="")   # login status
    info = detect_cli("codex", run=run, which=which)
    assert info.installed is True
    assert info.version == "codex-cli 0.146.0"
    assert info.logged_in is True


def test_detect_cli_missing_binary():
    info = detect_cli("claude", run=lambda *a, **k: None, which=lambda _b: None)
    assert info == CliInfo("claude", False, None, None)


def _which_ok(_b): return "/bin/" + _b

def _run_version_and_login(cmd, **k):
    from types import SimpleNamespace
    if cmd[1:2] == ["--version"]:
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.146.0")
    if cmd == ["codex", "login", "status"]:
        return SimpleNamespace(returncode=0, stdout="")
    if cmd[:2] == ["codex", "debug"]:
        return SimpleNamespace(returncode=0, stdout=_CODEX_JSON)
    return SimpleNamespace(returncode=0, stdout="")


def test_preflight_refuses_when_not_installed():
    pf = preflight_model("codex", "gpt-5.6-sol", which=lambda _b: None,
                         run=lambda *a, **k: None)
    assert pf.action == "refuse" and "not installed" in pf.message.lower()


def test_preflight_refuses_confidently_absent_model():
    pf = preflight_model("codex", "gpt-9.9-nope", which=_which_ok, run=_run_version_and_login)
    assert pf.action == "refuse"
    assert "gpt-9.9-nope" in pf.message and "gpt-5.6-sol" in pf.message   # names the served list


def test_preflight_proceeds_for_available_model():
    pf = preflight_model("codex", "gpt-5.6-sol", which=_which_ok, run=_run_version_and_login)
    assert pf.action == "proceed"


def test_preflight_warns_when_unknown():
    # installed + logged in, but discovery of the list fails -> None -> warn
    def run(cmd, **k):
        from types import SimpleNamespace
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.50.0")
        if cmd == ["codex", "login", "status"]:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout="")   # debug models fails, no cache
    pf = preflight_model("codex", "gpt-5.6-sol", which=_which_ok, run=run, home=Path("/nope"))
    assert pf.action == "warn"


def test_preflight_claude_alias_skips_the_probe_entirely(tmp_path, monkeypatch):
    # Aliases resolve server-side and are always valid -- preflight must
    # return proceed without ever touching list_models (Keychain+HTTPS probe).
    # NOTE: discovery.py's probe helpers wrap their calls in blanket
    # `except Exception: return None` (by design -- probe failure means
    # warn-and-proceed, never a crash), so a fake that *raises* to prove
    # "not called" would be silently absorbed and prove nothing. Track calls
    # instead. home=tmp_path + a deleted env var keep this hermetic even if a
    # regression lets the probe fall through to a real credentials file.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls: list[object] = []
    def run(cmd, **k):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="claude-cli 1.0")
        if cmd[:2] == ["claude", "auth"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True}))
        calls.append(cmd)                          # e.g. the keychain probe
        return SimpleNamespace(returncode=1, stdout="")
    def http(*a, **k):
        calls.append(("http", a, k))
        return SimpleNamespace(status_code=200, json=lambda: {"data": []})
    pf = preflight_model("claude", "opus", which=_which_ok, run=run, http=http, home=tmp_path)
    assert pf.action == "proceed"
    assert calls == []          # zero list_models / Keychain / HTTPS calls


def test_preflight_proceeds_without_a_pinned_model_and_makes_no_model_calls():
    # model="" (harness default): nothing to validate -> proceed, list_models never called
    calls = {"debug": 0}
    def run(cmd, **k):
        from types import SimpleNamespace
        if cmd[:2] == ["codex", "debug"]:
            calls["debug"] += 1
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.146.0")
        return SimpleNamespace(returncode=0, stdout="")
    pf = preflight_model("codex", "", which=_which_ok, run=run)
    assert pf.action == "proceed" and calls["debug"] == 0


# --- container-probe: discovery runs the operator CLI INSIDE the mutator image
# (image=...) so version + version-filtered catalog match what a run uses ------

from nethackers.harness import discovery as _disc  # noqa: E402


def test_container_run_wraps_operator_cli_in_docker_with_image_and_auth():
    seen = {}

    def _capture(argv, **k):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=_CODEX_JSON)

    run = _disc._container_run("nethackers/mutator:latest", run=_capture)
    run(["codex", "debug", "models"], capture_output=True, text=True)
    argv = seen["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    i = argv.index("nethackers/mutator:latest")
    assert argv[i + 1:] == ["codex", "debug", "models"]   # operator sub-argv after the image
    assert "-v" in argv[:i]                                # codex auth mount precedes the image


def test_container_run_leaves_host_only_commands_on_the_host():
    # the macOS keychain probe must NOT be wrapped in docker (no keychain in the
    # linux container) -- only codex/claude route into the image.
    seen = {}
    run = _disc._container_run("img", run=lambda argv, **k: seen.setdefault("argv", argv))
    run(["security", "find-generic-password", "-s", "x", "-w"])
    assert seen["argv"][0] == "security"


def test_container_run_unauthenticated_when_login_unresolvable(monkeypatch):
    # if auth can't be resolved the probe runs without a mount (warn, never block)
    def _raise(*a, **k):
        raise _disc.AuthUnavailable("codex", "hint")
    monkeypatch.setattr(_disc, "auth_docker_args", _raise)
    seen = {}
    run = _disc._container_run("img", run=lambda argv, **k: seen.setdefault("argv", argv))
    run(["codex", "debug", "models"])
    argv = seen["argv"]
    assert argv[:3] == ["docker", "run", "--rm"] and "-v" not in argv
    assert argv[-3:] == ["codex", "debug", "models"]


def test_list_models_with_image_probes_the_container():
    seen = {}

    def _capture(argv, **k):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=_CODEX_JSON)

    models = list_models("codex", image="my/mut:tag", run=_capture)
    assert models and models[0].id == "gpt-5.6-sol"
    assert seen["argv"][:3] == ["docker", "run", "--rm"] and "my/mut:tag" in seen["argv"]


def test_detect_cli_with_image_ignores_host_which_and_reads_container_version():
    # `which` must not gate in image mode -- the binary is baked into the image;
    # a host `which` miss would wrongly report not-installed on the host path.
    def _ver(argv, **k):
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.149.0")
    info = detect_cli("codex", image="img", run=_ver, which=lambda n: None)
    assert info.installed is True and info.version == "codex-cli 0.149.0"


# --- image not built: degrade to 'unknown', never the host CLI's stale cache --


def _inspect_fails(*a, **k):
    return SimpleNamespace(returncode=1, stdout="")   # `docker image inspect` -> absent


def test_list_models_image_absent_returns_none_not_host_cache():
    # the whole point of the container probe: an unbuilt image must NOT silently
    # fall back to the host's ~/.codex/models_cache.json (the wrong version).
    assert list_models("codex", image="nope:tag", run=_inspect_fails) is None


def test_detect_cli_image_absent_reports_not_installed():
    info = detect_cli("codex", image="nope:tag", run=_inspect_fails)
    assert info.installed is False and info.version is None


def test_codex_models_skips_host_cache_when_cache_disabled():
    # container mode passes allow_cache=False; a failed probe returns None rather
    # than reading a (wrong-version) host cache.
    assert _disc._codex_models(run=_inspect_fails, home=Path("/does/not/exist"),
                               allow_cache=False) is None
