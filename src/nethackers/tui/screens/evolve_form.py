"""Textual widget for the ``⚔ Evolve`` tab: a form that builds an
``EvolveParams`` from user input (owner/token defaulted from the logged-in
``Credentials``, mirroring what ``nethackers evolve`` derives from stored
creds for a terminal invocation) and hands it to
``harness.launch.prepare_evolve``. On success the returned ``EvolvePlan``
drives a pushed ``EvolveScreen`` -- the same live monitor the CLI path uses
-- so in-app and CLI launches share one code path from ``prepare_evolve``
onward.

Validation is deliberately minimal and synchronous: a blank objective or
non-integer iterations/token-budget raise ``ValueError`` from ``_params()``,
caught by the button handler and shown in ``#f_err``. There is nothing else
worth checking before a run starts -- ``prepare_evolve``/``run_loop``
surface any deeper failure (bad seed path, unreachable hub, ...) through the
pushed screen's own ``.error`` once the run is under way.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Select, Static

from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.screens.evolve import EvolveScreen


class EvolveForm(Vertical):
    """The ``⚔ Evolve`` tab: objective/seed/operator/iterations/token-budget
    inputs and a **Start** button that builds an ``EvolveParams``, calls
    ``prepare_evolve``, and pushes the live ``EvolveScreen`` monitor over
    the dashboard."""

    def __init__(self, hub: str, creds: Credentials | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._creds = creds

    def compose(self) -> ComposeResult:
        yield Label("⚔  Start an evolve run")
        yield Input(placeholder="objective (e.g. wiz-elf-cha-mal)", id="f_obj")
        yield Input(value="roots/autoascend", placeholder="seed", id="f_seed")
        yield Select(
            [("claude", "claude"), ("codex", "codex")],
            value="claude", allow_blank=False, id="f_op",
        )
        yield Input(value="1", placeholder="iterations", id="f_iters")
        yield Input(value="200000", placeholder="token budget", id="f_budget")
        yield Button("Start", id="f_start", variant="success")
        yield Static("", id="f_err")

    def _params(self) -> EvolveParams:
        obj = self.query_one("#f_obj", Input).value.strip()
        if not obj:
            raise ValueError("objective is required")
        try:
            iters = int(self.query_one("#f_iters", Input).value)
            budget = int(self.query_one("#f_budget", Input).value)
        except ValueError:
            raise ValueError("iterations and token-budget must be integers") from None
        return EvolveParams(
            objective=obj,
            seed=self.query_one("#f_seed", Input).value.strip(),
            operator=str(self.query_one("#f_op", Select).value),
            iterations=iters,
            token_budget=budget,
            hub=self._hub,
            token=self._creds.token if self._creds else "dev-token",
            owner=self._creds.login if self._creds else "dev",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "f_start":
            return
        try:
            params = self._params()
        except ValueError as exc:
            self.query_one("#f_err", Static).update(f"[red]{exc}[/red]")
            return
        plan = prepare_evolve(params)
        self.app.push_screen(EvolveScreen(plan.cfg, run=plan.run))
