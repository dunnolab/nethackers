import shutil

import pytest

from nethackers.harness.discovery import list_models


@pytest.mark.codex_live
def test_codex_debug_models_returns_real_catalog():
    if shutil.which("codex") is None:
        pytest.skip("codex CLI not on PATH")
    models = list_models("codex")
    assert models is not None and len(models) > 0
    assert all(m.id for m in models)


@pytest.mark.claude_live
def test_claude_v1_models_returns_real_catalog():
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")
    models = list_models("claude")
    # None is acceptable here (stale token / no keychain in CI); when present,
    # it must be a real, non-empty catalog.
    assert models is None or (len(models) > 0 and all(m.id for m in models))
