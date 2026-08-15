"""Widget-level tests for ``EvolveForm`` -- the ``⚔ Evolve`` tab's launch
form (Task 16): **Start** builds an ``EvolveParams`` (owner/token defaulted
from the logged-in ``Credentials``), calls ``harness.launch.prepare_evolve``,
and pushes the ``EvolveScreen`` monitor with the returned plan. A blank
objective must show its error in ``#f_err`` and must NOT call
``prepare_evolve`` or push anything.

``prepare_evolve`` is monkeypatched on the ``evolve_form`` module in every
test here -- the real one builds a run dir on disk and spins up an operator,
which this suite must never do."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Input, Static

import nethackers.tui.screens.evolve_form as ef
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.screens.evolve import EvolveScreen
from nethackers.tui.screens.evolve_form import EvolveForm


class _Host(App):
    def __init__(self, creds: Credentials | None) -> None:
        super().__init__()
        self._creds = creds

    def compose(self) -> ComposeResult:
        yield EvolveForm("http://h", self._creds, id="evolve")


class _Plan:
    """Stand-in for ``EvolvePlan`` -- only ``.cfg``/``.run`` are read by
    ``EvolveForm``/``EvolveScreen.__init__`` before ``push_screen`` (itself
    monkeypatched below) intercepts the call, so nothing else is needed."""

    cfg = "CFG"
    run = staticmethod(lambda cb=None: [])


async def test_start_builds_params_and_pushes_monitor(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test() as pilot:
        app.query_one("#f_obj", Input).value = "wiz-elf-cha-mal"
        monkeypatch.setattr(app, "push_screen", lambda s: seen.update(screen=s))

        await pilot.click("#f_start")
        await pilot.pause()

        assert seen["params"].objective == "wiz-elf-cha-mal"
        assert seen["params"].owner == "castiel"
        assert seen["params"].token == "tok"
        assert isinstance(seen.get("screen"), EvolveScreen)


async def test_missing_objective_shows_error_no_push(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        ef, "prepare_evolve", lambda *a, **k: seen.update(called=True)
    )
    app = _Host(None)
    async with app.run_test() as pilot:
        await pilot.click("#f_start")
        await pilot.pause()

        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "objective" in err_text
        assert "called" not in seen
