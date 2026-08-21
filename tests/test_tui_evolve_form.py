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
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.app import NetHackersApp
from nethackers.tui.screens.evolve_form import EvolveForm


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
    # the form runs the sandbox preflight before prepare_evolve; stub it green
    # so these wiring tests proceed (no real docker/login is touched). The
    # failure path is covered by test_preflight_failure_shows_error_no_start.
    monkeypatch.setattr(ef, "sandbox_preflight", lambda *a, **k: None)


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
