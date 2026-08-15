"""Textual widget for the ``⚔ Evolve`` tab: a form that builds an
``EvolveParams`` from user input (owner/token defaulted from the logged-in
``Credentials``, mirroring what ``nethackers evolve`` derives from stored
creds) and hands it to ``harness.launch.prepare_evolve``. On success the
returned ``EvolvePlan`` drives a pushed ``EvolveScreen`` -- the same live
monitor the CLI path uses.

Objective is a **filter + pick** over the catalog (74 entries: ``random``
plus the 73 identities; ``all`` is excluded -- it's a hub-query marker with
no episodes to evolve against), so you type a fragment (``wiz``, ``val``)
and choose from the narrowed list instead of typing an exact
``role-race-align-gender`` string. Seed root is a dropdown of the solution
roots discovered under ``roots/`` (dirs with a ``nethackers.solution.json``).
The evolve loop consumes a single objective (``CATALOG[name]``); a
multi-objective picker awaits loop support and is not wired here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.hub.objectives import CATALOG
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.screens.evolve import EvolveScreen

# random first (the north-star), then the identities sorted; drop "all".
_OBJECTIVES: list[str] = ["random"] + sorted(k for k in CATALOG if k not in ("random", "all"))


def _seed_roots() -> list[str]:
    """Solution roots discovered under ``roots/`` (a dir with a
    ``nethackers.solution.json``), relative to the CWD; falls back to the
    canonical ``roots/autoascend`` when nothing is found."""
    found: list[str] = []
    base = Path("roots")
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "nethackers.solution.json").exists():
                found.append(child.as_posix())
    return found or ["roots/autoascend"]


class EvolveForm(Vertical):
    """The ``⚔ Evolve`` tab: filter-and-pick an objective, choose a seed root
    and operator, set iterations/token-budget, and **Start** -- which builds
    an ``EvolveParams``, calls ``prepare_evolve``, and pushes the live
    ``EvolveScreen`` monitor over the dashboard."""

    DEFAULT_CSS = """
    EvolveForm { align: center middle; }
    EvolveForm > #form { width: 74; height: auto; padding: 1 2; }
    EvolveForm Label { text-style: bold; color: #d2a24c; margin-top: 1; }
    EvolveForm #f_obj_list { height: 7; border: round #7c745f; }
    EvolveForm #f_obj_sel { color: #d7c9a2; }
    EvolveForm #f_start { margin-top: 1; width: 100%; }
    EvolveForm #f_err { height: auto; color: #c04040; }
    """

    def __init__(self, hub: str, creds: Credentials | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._creds = creds
        self._objective: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="form", classes="panel"):
            yield Label("Objective — type to filter, then pick one")
            yield Input(placeholder="filter…  e.g. wiz · val · random", id="f_obj_filter")
            yield OptionList(*(Option(o, id=o) for o in _OBJECTIVES), id="f_obj_list")
            yield Static("[dim]none selected[/]", id="f_obj_sel")
            yield Label("Seed root")
            roots = _seed_roots()
            yield Select(((r, r) for r in roots), value=roots[0], allow_blank=False, id="f_seed")
            yield Label("Operator")
            yield Select(
                [("claude", "claude"), ("codex", "codex")],
                value="claude", allow_blank=False, id="f_op",
            )
            yield Label("Iterations")
            yield Input(value="1", id="f_iters")
            yield Label("Token budget")
            yield Input(value="200000", id="f_budget")
            yield Button("Start", id="f_start", variant="success")
            yield Static("", id="f_err")

    def on_mount(self) -> None:
        self.query_one("#form").border_title = "⚔ Start an Evolve Run"

    def on_input_changed(self, event: Input.Changed) -> None:
        """Narrow the objective list as the filter is typed."""
        if event.input.id != "f_obj_filter":
            return
        q = event.value.strip().lower()
        matches = [o for o in _OBJECTIVES if q in o.lower()]
        options = self.query_one("#f_obj_list", OptionList)
        options.clear_options()
        options.add_options([Option(o, id=o) for o in matches])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._objective = event.option.id
        self.query_one("#f_obj_sel", Static).update(f"objective: [b]{self._objective}[/]")

    def _params(self) -> EvolveParams:
        if not self._objective:
            raise ValueError("pick an objective from the list")
        try:
            iters = int(self.query_one("#f_iters", Input).value)
            budget = int(self.query_one("#f_budget", Input).value)
        except ValueError:
            raise ValueError("iterations and token-budget must be integers") from None
        return EvolveParams(
            objective=self._objective,
            seed=str(self.query_one("#f_seed", Select).value),
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
