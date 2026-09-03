"""Suite-wide test fixtures."""
import pytest

import nethackers.diagnostics as _diagnostics
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
    # Start/probe now resolve the container runtime (docker/podman -- issue #50)
    # via `container_runtime()`, a real `<exe> info` subprocess. Pin it here so
    # no TUI test shells out through the form on mount or Start.
    monkeypatch.setattr(_ef, "container_runtime", lambda **k: "docker", raising=False)


@pytest.fixture(autouse=True)
def _hermetic_tui_readiness(monkeypatch):
    # EvolveForm's on-mount readiness worker (spec 5.8) calls
    # diagnostics.run_checks(..., only=evolve_ids) -- scoped to the 4 LOCAL
    # evolve checks (container_runtime/arena_image/mutator_image/operator),
    # so the network-touching checks (hub HTTPS, gh subprocess, creds-file
    # read) are never even run -- that guarantee now lives at the source
    # (diagnostics.run_checks' `only` filter + the worker's scoping to
    # evolve_ids), not here. What's left un-injected even with `only` applied
    # is still real local I/O though: `docker_available`/`image_present` (a
    # real `docker` subprocess) and `preflight_operator` (a host CLI login
    # probe). Stub it suite-wide by name, same hermetic-suite rule as
    # `_hermetic_tui_discovery` above, so no OTHER test file that happens to
    # mount EvolveForm/NetHackersApp shells out to docker on every mount just
    # for a strip it never looks at (`run_checks` is looked up as a fresh
    # `diagnostics.run_checks` module attribute at call time, not a name bound
    # into evolve_form's own namespace, so patching it here is the seam the
    # form actually dereferences). The readiness tests in
    # test_tui_evolve_form.py override this with their own fake.
    monkeypatch.setattr(_diagnostics, "run_checks", lambda **k: [], raising=False)


@pytest.fixture(autouse=True)
def _hermetic_tui_publish(monkeypatch):
    # EvolveForm's Start handler (spec 5.6's publish-readiness pre-check) now
    # also calls gh_state() -- a real `gh` subprocess that, on any box where
    # `gh` is installed and authenticated (true of a developer's own machine,
    # and possibly a CI runner with a GH_TOKEN in its env), makes a live call
    # to the GitHub API. Reachable from EVERY test in test_tui_evolve_form.py
    # that presses Start with a real (non-offline) owner, not just the ones
    # that test the warning itself. Stub it suite-wide by name (same
    # hermetic-suite rule as `_hermetic_tui_discovery` above, patched on
    # evolve_form's own imported binding, which is the seam
    # `_publish_warning` actually dereferences): default to "authed" so a
    # test that presses Start without caring about publish-readiness sees
    # today's behavior (no #f_publish_warn text, same as before this check
    # existed). Tests that exercise the warning itself override this with
    # their own fake.
    monkeypatch.setattr(_ef, "gh_state", lambda: ("stub", "authed"), raising=False)


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
