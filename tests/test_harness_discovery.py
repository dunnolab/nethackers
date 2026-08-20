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
