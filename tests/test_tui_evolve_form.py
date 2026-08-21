"""Widget-level tests for ``EvolveForm`` -- the ``⚔ Evolve`` tab's launch
form: **Start** builds an ``EvolveParams`` (owner/token defaulted from the
logged-in ``Credentials``), calls ``harness.launch.prepare_evolve``, and hands
the plan to ``app.start_run`` (which registers a background run + opens its
monitor). A blank objective shows its error in ``#f_err`` and must NOT call
``prepare_evolve`` or start a run.

``prepare_evolve`` is monkeypatched on the ``evolve_form`` module in every
test -- the real one builds a run dir on disk and spins up an operator, which
this suite must never do."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Select, Static

import nethackers.tui.screens.evolve_form as ef
from nethackers.harness.discovery import CliInfo, ModelInfo
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.evolve_form import EvolveForm


@pytest.fixture(autouse=True)
def _no_live_models(monkeypatch):
    # Default: discovery "unavailable" -> the form keeps its static fallback,
    # so every existing test sees exactly today's behavior (and no real probe).
    # detect_cli is stubbed too so the version-line worker never shells out.
    monkeypatch.setattr(ef, "list_models", lambda *a, **k: None)
    monkeypatch.setattr(ef, "detect_cli",
                        lambda backend, **k: CliInfo(backend, True, f"{backend} 9.9.9", True))


class _Host(App):
    """Stands in for NetHackersApp: the form calls ``self.app.start_run``."""

    def __init__(self, creds: Credentials | None) -> None:
        super().__init__()
        self._creds = creds
        self.started: object | None = None  # the plan handed to start_run

    def compose(self) -> ComposeResult:
        yield EvolveForm("http://h", self._creds, id="evolve")

    def start_run(self, plan: object) -> None:
        self.started = plan


class _Plan:
    """Stand-in for ``EvolvePlan`` -- ``prepare_evolve`` (monkeypatched) returns
    it and the host's ``start_run`` (also a stand-in) just records it."""

    rid = "run-x"
    cfg = "CFG"
    run = staticmethod(lambda cb=None: [])


@pytest.fixture(autouse=True)
def _sandbox_preflight_ok(monkeypatch):
    # the form runs the sandbox preflight + image check before prepare_evolve;
    # stub both green (preflight ok, image already built) so these wiring tests
    # launch straight without touching real docker/login. The failure path is
    # covered by test_preflight_failure_shows_error_no_start; the auto-build
    # path by test_missing_image_builds_then_launches.
    monkeypatch.setattr(ef, "sandbox_preflight", lambda *a, **k: None)
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: True)


async def test_start_builds_params_and_starts_a_run(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        # objective is chosen from the filter+list; set the selection directly
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()

        assert seen["params"].objective == "wiz-elf-cha-mal"
        assert seen["params"].owner == "castiel"
        assert seen["params"].token == "tok"
        assert seen["params"].model is None and seen["params"].effort is None  # unpinned
        assert isinstance(app.started, _Plan)  # the plan was handed to start_run


async def test_start_pins_model_and_effort_from_the_pickers(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 50)) as pilot:
        form = app.query_one(ef.EvolveForm)
        form._objective = "wiz-elf-cha-mal"
        form.query_one("#f_op", Select).value = "codex"  # -> repopulates model list
        await pilot.pause()
        form.query_one("#f_model", Select).value = "gpt-5.6-sol"
        form.query_one("#f_effort", Select).value = "max"
        await pilot.pause()
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert seen["params"].operator == "codex"
        assert seen["params"].model == "gpt-5.6-sol"
        assert seen["params"].effort == "max"


async def test_form_scroll_pane_is_not_a_nav_target():
    # regression: making #form a VerticalScroll turned the whole-form panel into
    # a focusable nav stop that shadowed the fields -- the operator select became
    # unreachable/"not selectable". The pane must scroll without being a stop.
    app = NetHackersApp(hub="http://h", creds=None, start="evolve")
    async with app.run_test(size=(100, 42)) as pilot:
        await pilot.pause()
        await pilot.pause()
        targets = app._nav_targets()
        assert app.query_one("#form") not in targets          # the scroll pane isn't a stop
        assert app.query_one("#f_op", Select) in targets       # but the operator is
        assert app.query_one("#f_model", Select) in targets    # and the new pickers
        assert app.query_one("#f_effort", Select) in targets


async def test_custom_model_reveals_freetext_and_flows_through(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        form = app.query_one(ef.EvolveForm)
        form._objective = "wiz-elf-cha-mal"
        custom = form.query_one("#f_model_custom", Input)
        assert custom.display is False  # hidden until Custom… is picked
        form.query_one("#f_model", Select).value = "__custom__"
        await pilot.pause()
        assert custom.display is True
        custom.value = "my-exp-model-42"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert seen["params"].model == "my-exp-model-42"


async def test_missing_objective_shows_error_no_start(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: seen.update(called=True))
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert "called" not in seen        # prepare_evolve NOT called
        assert app.started is None         # no run started
        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "objective" in err_text


async def test_preflight_failure_shows_error_no_start(monkeypatch):
    """A valid form whose sandbox preflight fails (no runtime / no login) shows
    the preflight's message in #f_err and must NOT call prepare_evolve or start
    a run -- the same fail-fast the CLI gives, surfaced in the form."""
    seen: dict = {}
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: seen.update(called=True))
    monkeypatch.setattr(
        ef, "sandbox_preflight",
        lambda *a, **k: "[red]sandbox unavailable[/]: start colima (or Podman)")
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert "called" not in seen        # prepare_evolve blocked by the preflight
        assert app.started is None          # no run started
        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "sandbox unavailable" in err_text


async def test_model_picker_populates_from_live_discovery(monkeypatch):
    monkeypatch.setattr(
        ef, "list_models",
        lambda backend, **k: (
            [ModelInfo("live-sol-9", "Live Sol 9")] if backend == "claude" else None
        ),
    )
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        sel = app.query_one("#f_model", Select)
        sel.value = "live-sol-9"                 # raises if the live option isn't present
        assert sel.value == "live-sol-9"


async def test_model_picker_dispatches_exactly_once_on_mount(monkeypatch):
    # Regression: the default operator's Select is built with a non-blank
    # initial value, so mounting it organically fires one Select.Changed
    # (-> one _refresh_models("claude")) all on its own. An extra explicit
    # kick from on_mount would double-dispatch list_models -- two real
    # Keychain+HTTP round-trips per form mount in production. Lock it at one.
    calls: list[str] = []

    def _counting_list_models(backend, **k):
        calls.append(backend)
        return None

    monkeypatch.setattr(ef, "list_models", _counting_list_models)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == ["claude"]        # exactly one dispatch on mount, not two


async def test_operator_version_line_shows_detected_cli(monkeypatch):
    # The form surfaces the detected operator CLI's version (spec 3C) via the
    # same single discovery worker that populates the model list.
    monkeypatch.setattr(ef, "detect_cli",
                        lambda backend, **k: CliInfo(backend, True, "claude 2.1.237", True))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        line = str(app.query_one("#f_op_version", Static).render())
        assert "2.1.237" in line          # detected version shown for the default operator


async def test_operator_version_line_shows_not_found_when_missing(monkeypatch):
    monkeypatch.setattr(ef, "detect_cli",
                        lambda backend, **k: CliInfo(backend, False, None, None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        line = str(app.query_one("#f_op_version", Static).render()).lower()
        assert "not found" in line         # missing binary flagged, not a crash


async def test_effort_options_follow_selected_model(monkeypatch):
    # The Reasoning-effort picker is driven by the selected model's discovered
    # efforts (not a hardcoded list); a model with no reasoning falls back to
    # the shared static EFFORTS.
    monkeypatch.setattr(
        ef, "list_models",
        lambda backend, **k: (
            [ModelInfo("m-rich", "Rich", ("low", "high", "ultra"), False),
             ModelInfo("m-bare", "Bare", (), False)]
            if backend == "claude" else None
        ),
    )
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        assert form._effort_options("m-rich") == [
            ("Harness default", ""), ("low", "low"), ("high", "high"), ("ultra", "ultra")]
        assert form._effort_options("m-bare") == [
            ("Harness default", ""), *((e, e) for e in ef.EFFORTS)]   # fallback
        form.query_one("#f_model", Select).value = "m-rich"
        await pilot.pause()
        eff = form.query_one("#f_effort", Select)
        eff.value = "ultra"                # a live-only level absent from static EFFORTS
        assert eff.value == "ultra"        # picker was repopulated from live reasoning


async def test_missing_image_builds_then_launches(monkeypatch):
    """When the sandbox image isn't built, Start builds it (off the UI thread,
    streaming progress into #f_err) and launches once ready -- the user never
    runs `make`."""
    seen: dict = {}

    def _fake_prepare(p, **k):
        seen["prepared"] = True
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare)
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)   # not built -> build path
    built: dict = {}

    def _build(image, on_line=None, **k):
        built["image"] = image
        if on_line:
            on_line("compiling nle…")     # exercises the streamed-progress path
        return None                        # success

    monkeypatch.setattr(ef, "build_mutator_image", _build)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()   # the build-then-launch worker
        await pilot.pause()
        assert built.get("image")                # the image was auto-built
        assert seen.get("prepared") is True       # prepare_evolve ran after the build
        assert isinstance(app.started, _Plan)     # and the plan reached start_run
