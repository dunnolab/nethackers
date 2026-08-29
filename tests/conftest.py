"""Suite-wide test fixtures."""
import pytest

import nethackers.harness.launch as _launch
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


@pytest.fixture(autouse=True)
def _hermetic_provenance(monkeypatch):
    # launch.prepare_evolve's provenance fields (arena/mutator image digest,
    # operator version) fall back to _default_image_digest/
    # _default_operator_version when a test doesn't inject its own resolver
    # -- both shell out to a real `docker image inspect` / `docker run ...
    # --version`. On a box with the images already built locally that's a
    # real, multi-second container spin-up on every evolve-wiring test, not
    # just the tests that actually exercise provenance (the hermetic-suite
    # rule). Stub both suite-wide by name; prepare_evolve looks these up
    # dynamically at call time (see launch.py), not via a bound-at-def-time
    # kwarg default, so the patch always takes effect. Tests that inject
    # their own resolver, or want to exercise the real default, are
    # unaffected -- test_provenance.py does both (an injected resolver wins
    # outright; re-patching these same names overrides this fixture).
    monkeypatch.setattr(_launch, "_default_image_digest", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(_launch, "_default_operator_version", lambda *a, **kw: None, raising=False)


# The NETHACKERS_* stage keys that `load_stage`/`_find_stack_file` consume.
_STAGE_ENV_KEYS = (
    "NETHACKERS_STAGE", "NETHACKERS_HUB", "NETHACKERS_HUB_PORT",
    "COMPOSE_PROJECT_NAME", "NETHACKERS_DATA_ROOT", "NETHACKERS_REPO_NAME",
    "NETHACKERS_ARENA_IMAGE", "NETHACKERS_MUTATOR_IMAGE", "NETHACKERS_CLIENT_ID",
    "NETHACKERS_STAGE_FILE",
)


@pytest.fixture
def clean_stage(monkeypatch, tmp_path):
    """Opt-in isolation for tests that assert stage DEFAULTS: chdir into an
    empty dir (so no ancestor `.env.stack` is on the walk-up path) and clear
    every NETHACKERS_* stage key -- so a developer's real `.env.stack` in this
    worktree (e.g. left by `make up`) can't leak into the resolved Stage. A
    test may re-`setenv` a specific key afterwards to exercise the env layer."""
    monkeypatch.chdir(tmp_path)
    for _k in _STAGE_ENV_KEYS:
        monkeypatch.delenv(_k, raising=False)
    return tmp_path
