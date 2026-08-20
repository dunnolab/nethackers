import json
from types import SimpleNamespace

from nethackers.harness.discovery import ModelInfo, list_models

_CODEX_JSON = json.dumps({"models": [
    {"slug": "gpt-5.6-sol", "display_name": "gpt-5.6-sol",
     "visibility": "list", "supported_reasoning_levels": ["low", "high"], "upgrade": None},
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


_ANTHROPIC_OK = {"data": [
    {"id": "claude-opus-5", "display_name": "Opus 5"},
    {"id": "claude-fable-5", "display_name": "Fable 5"},
], "has_more": False}


def _http(status, payload):
    def get(url, **kwargs):
        return SimpleNamespace(status_code=status, json=lambda: payload)
    return get


def test_list_models_claude_from_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")   # 3rd-tier credential
    models = list_models("claude", http=_http(200, _ANTHROPIC_OK), home=tmp_path)
    assert [m.id for m in models] == ["claude-opus-5", "claude-fable-5"]
    assert models[0].label == "Opus 5"


def test_list_models_claude_401_is_unknown_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stale")
    # 401 = stale token, NOT "no models" -> None (warn+proceed), never []
    assert list_models("claude", http=_http(401, {}), home=tmp_path) is None


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
        return SimpleNamespace(status_code=200, json=lambda: _ANTHROPIC_OK)
    def _run_fail(*a, **k):   # force past the keychain tier
        return SimpleNamespace(returncode=1, stdout="")
    models = list_models("claude", run=_run_fail, http=get, home=tmp_path)
    assert [m.id for m in models] == ["claude-opus-5", "claude-fable-5"]
    assert seen["auth"] == "Bearer oauth-xyz"     # token used, never logged
