"""Suite-wide test fixtures."""
import pytest

import nethackers.tui.screens.evolve_form as _ef
from nethackers.harness.discovery import CliInfo


@pytest.fixture(autouse=True)
def _hermetic_tui_discovery(monkeypatch):
    # tui/app.py mounts EvolveForm eagerly, and its on-mount worker calls
    # detect_cli + list_models -- real subprocess + network + Keychain. Stub
    # both suite-wide so NO test shells out or hits the network through the form
    # (the hermetic-suite rule). This patches the names in the evolve_form
    # module only, not discovery itself, so the discovery unit tests -- which
    # inject their own run/http/which -- are unaffected. Evolve-form tests
    # override these locally when they need specific values.
    monkeypatch.setattr(_ef, "detect_cli",
                        lambda backend, **k: CliInfo(backend, True, f"{backend} 0.0.0", True),
                        raising=False)
    monkeypatch.setattr(_ef, "list_models", lambda *a, **k: None, raising=False)
