"""Suite-wide test fixtures."""
import pytest

import nethackers.tui.screens.evolve_form as _ef
from nethackers.harness.discovery import CliInfo


@pytest.fixture(autouse=True)
def _hermetic_tui_discovery(monkeypatch):
    # tui/app.py mounts EvolveForm eagerly, and its on-mount worker calls
    # probe_operator -- a real `docker run` (subprocess + Keychain + network).
    # Stub it suite-wide so NO test shells out through the form (the hermetic-
    # suite rule). This patches the name in the evolve_form module only, not
    # discovery itself, so the discovery unit tests -- which inject their own
    # run/http -- are unaffected. Evolve-form tests override it when they need
    # specific values.
    monkeypatch.setattr(
        _ef, "probe_operator",
        lambda backend, **k: (CliInfo(backend, True, f"{backend} 0.0.0", True), None),
        raising=False)
