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

from textual.app import App, ComposeResult
from textual.widgets import Static

import nethackers.tui.screens.evolve_form as ef
from nethackers.hubclient.credentials import Credentials
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
        await pilot.click("#f_start")
        await pilot.pause()

        assert seen["params"].objective == "wiz-elf-cha-mal"
        assert seen["params"].owner == "castiel"
        assert seen["params"].token == "tok"
        assert isinstance(app.started, _Plan)  # the plan was handed to start_run


async def test_missing_objective_shows_error_no_start(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: seen.update(called=True))
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.click("#f_start")
        await pilot.pause()
        assert "called" not in seen        # prepare_evolve NOT called
        assert app.started is None         # no run started
        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "objective" in err_text
