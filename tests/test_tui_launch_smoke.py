"""Regression smoke test: the REAL ``resolve_image`` -> ``probe_operator``
mount-time discovery path (spec Appendix B / companion doc §8), against a
no-docker world -- plus a guard for the ``_refresh_models`` worker hardening.

``tests/conftest.py``'s autouse ``_hermetic_tui_discovery`` stubs
``evolve_form.probe_operator`` in EVERY other test in the suite, so nothing
else exercises the real call ``EvolveForm``'s mount fires: ``#f_op``'s Select
is built with a non-blank initial value, so mounting it organically fires one
``Select.Changed`` -> ``on_select_changed`` -> ``_maybe_refresh_models`` ->
``_refresh_models`` (a ``@work`` thread) -> ``probe_operator``. That's exactly
the path Plan 2 fixed: ``resolve_image`` never returns ``None``, and
``probe_operator`` returns ``installed=False`` -- without ever touching
docker -- when the resolved image isn't present locally. Left stubbed
suite-wide, that fix is structurally untested (companion doc §8: "the test
suite stays green while the real TUI would crash at launch"). These tests
deliberately override the stub back to the genuine callables so a regression
here is actually caught.

Seam note (verified with a standalone repro, not assumed): ``probe_operator``'s
own ``run: Callable = subprocess.run`` parameter -- and ``image_present``'s,
and ``resolve_image``'s ``repo_root`` -- are bound to the real function
objects at MODULE IMPORT time, a plain Python default-argument capture.
Monkeypatching ``discovery.subprocess.run`` (or ``shutil.which``) afterwards
is never actually consulted by a call that doesn't pass ``run=``/``repo_root=``
explicitly, since the pre-bound default still references the original
function object. ``image_present`` itself, by contrast, is a bare global name
``probe_operator`` looks up fresh from ``discovery``'s module namespace on
every call, so patching THAT is the seam that's actually live -- the same
"patch the importing module's own copy of the name" trick
``_hermetic_tui_discovery`` already relies on for ``probe_operator``.
"""
from __future__ import annotations

import asyncio
import threading

import nethackers.harness.discovery as disc
import nethackers.tui.screens.evolve_form as ef
from nethackers.tui.app import NetHackersApp

# A guaranteed-dead hub: loopback on a port nothing listens on, so the
# mount-time hub-mode probe (`_fetch_hub_mode`) fails fast with no real DNS
# or network dependency -- same reasoning as test_tui_app.py's `_DEAD_HUB`.
_DEAD_HUB = "http://127.0.0.1:1"


def _fake_image_absent(image: object, **_kw: object) -> bool:
    """Stand-in for ``sandbox_preflight.image_present`` that simulates "no
    docker / image never pulled": the real function would run ``docker image
    inspect <image>`` and get a non-zero exit (or an OSError if docker itself
    is missing) -- either way ``False``, with no container ever started.
    Still asserts ``image`` is the real resolved string ``resolve_image`` is
    contracted to always produce (never ``None``), so a regression THERE
    (Appendix B: "else None reaches docker as a TypeError") is a loud failure
    here, not silently swallowed by this fake."""
    assert isinstance(image, str) and image, f"resolve_image regressed: got {image!r}"
    return False


async def test_bare_launch_mounts_evolve_form_without_docker(monkeypatch):
    """A plain dashboard mount -- no docker, image never pulled/built -- must
    not crash. Bypasses the autouse hermetic stub so the REAL
    resolve_image -> probe_operator path runs end to end; only the innermost
    "is this image already local" check is faked, so the test is deterministic
    regardless of what's actually built on the host running it."""
    monkeypatch.setattr(ef, "probe_operator", disc.probe_operator)  # the genuine callable
    monkeypatch.setattr(disc, "image_present", _fake_image_absent)

    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()  # let the mount-time probe actually finish
        await pilot.pause()
    assert app.error is None


async def test_model_probe_worker_survives_a_raising_probe_operator(monkeypatch):
    """Hardening guard: ``evolve_form.py``'s ``_refresh_models`` @work now
    carries ``exit_on_error=False``, matching ``app.py``'s ``_fetch_hub_mode``
    precedent -- so even if a future ``probe_operator`` stops being
    exception-safe, an unhandled exception inside the worker must not crash
    the app. Deliberately avoids ``workers.wait_for_complete()``: Textual's
    ``Worker.wait()`` re-raises ``WorkerFailed`` for a failed worker
    regardless of ``exit_on_error`` (that flag only gates whether the APP
    treats it as fatal), which would fail this test for the wrong reason.
    Also avoids polling ``app.workers`` for the worker's own state: the
    manager's done-callback discards a worker from its tracked set the
    instant it finishes (``WorkerManager._remove_worker``), so a completed
    worker is usually already gone by the time a poll observes it -- a
    ``threading.Event`` the fake itself sets (the same pattern
    ``test_tui_app.py``'s background-thread tests use) is the reliable
    signal instead."""
    ran = threading.Event()

    def _boom(backend, **_k):
        ran.set()
        raise RuntimeError("simulated probe_operator crash")
    monkeypatch.setattr(ef, "probe_operator", _boom)

    app = NetHackersApp(hub=_DEAD_HUB, creds=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(200):  # up to ~2s
            if ran.is_set():
                break
            await asyncio.sleep(0.01)
        await pilot.pause()  # let the worker fully return before we check .error
    assert app.error is None
