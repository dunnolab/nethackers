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
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.models import EFFORTS, MODELS
from nethackers.harness.sandbox_preflight import preflight as sandbox_preflight
from nethackers.hub.objectives import CATALOG
from nethackers.hubclient.credentials import Credentials

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

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
    ``EvolveScreen`` monitor over the dashboard.

    A focused text ``Input`` (the objective filter, iterations, budget)
    swallows printable keys, so the shell's advertised ``q`` quit -- and the
    ``1``–``6`` switches -- go dead while you're typing in one. ``escape``
    (``action_leave_field``) hands focus back to the main nav so those global
    keys work again: the way out of a field a stuck user reaches for."""

    BINDINGS = [Binding("escape", "leave_field", "Back to menu", show=False)]

    DEFAULT_CSS = """
    EvolveForm { align: center middle; }
    EvolveForm > #form { width: 74; height: auto; max-height: 100%; padding: 1 2; }
    EvolveForm Label { text-style: bold; color: #d2a24c; margin-top: 1; }
    EvolveForm #f_obj_list { height: 6; border: round #7c745f; }
    EvolveForm #f_obj_sel { color: #d7c9a2; }
    EvolveForm #f_start { margin-top: 1; width: 100%; }
    EvolveForm #f_err { height: auto; color: #c04040; }
    EvolveForm #f_model_custom { display: none; }  /* shown only for Custom… */
    """

    def __init__(self, hub: str, creds: Credentials | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._creds = creds
        self._objective: str | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form", classes="panel"):
            yield Label("Objective — type to filter, then pick one · esc to leave")
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
            yield Label("Model")
            yield Select(self._model_options("claude"), value="",
                         allow_blank=False, id="f_model")
            yield Input(placeholder="custom model id…", id="f_model_custom")
            yield Label("Reasoning effort")
            yield Select([("Harness default", ""), *((e, e) for e in EFFORTS)],
                         value="", allow_blank=False, id="f_effort")
            yield Label("Iterations")
            yield Input(value="1", id="f_iters")
            yield Button("Start", id="f_start", variant="success")
            yield Static("", id="f_err")

    def on_mount(self) -> None:
        form = self.query_one("#form")
        form.border_title = "⚔ Start an Evolve Run"
        # the scroll pane holds the fields but is NOT itself a nav stop -- else
        # the whole-form panel (a focusable .panel) competes with every field
        # for the cursor. It still scrolls: each field's scroll_visible() drives
        # it as the cursor lands.
        form.can_focus = False

    def action_leave_field(self) -> None:
        """Hand control back to the modal keyboard nav so the global keys
        (``q`` to quit, ``1``–``6`` to switch) and the arrow cursor work again
        -- a focused ``Input`` otherwise swallows them as text. Falls back to a
        plain blur if the app isn't the dashboard shell (form mounted alone)."""
        leave = getattr(self.app, "leave_to_nav", None)
        if callable(leave):
            leave()
        else:
            self.app.set_focus(None)

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

    @staticmethod
    def _model_options(backend: str) -> list[tuple[str, str]]:
        # "" is the harness default (no --model pin); "__custom__" reveals the
        # free-text id field. Curated list per backend from harness.models.
        return [("Harness default", ""), *MODELS.get(backend, []), ("Custom…", "__custom__")]

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "f_op":  # repopulate the model list for the new harness
            model = self.query_one("#f_model", Select)
            model.set_options(self._model_options(str(event.value)))
            model.value = ""
            self.query_one("#f_model_custom", Input).display = False
        elif event.select.id == "f_model":  # reveal the id field only for Custom…
            self.query_one("#f_model_custom", Input).display = event.value == "__custom__"

    def _model(self) -> str | None:
        value = str(self.query_one("#f_model", Select).value)
        if value == "__custom__":
            return self.query_one("#f_model_custom", Input).value.strip() or None
        return value or None  # "" (harness default) -> None

    def _effort(self) -> str | None:
        return str(self.query_one("#f_effort", Select).value) or None

    def _params(self) -> EvolveParams:
        if not self._objective:
            raise ValueError("pick an objective from the list")
        try:
            iters = int(self.query_one("#f_iters", Input).value)
        except ValueError:
            raise ValueError("iterations must be an integer") from None
        return EvolveParams(
            objective=self._objective,
            seed=str(self.query_one("#f_seed", Select).value),
            operator=str(self.query_one("#f_op", Select).value),
            iterations=iters,
            model=self._model(),
            effort=self._effort(),
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
        # The mutator always runs sandboxed -- surface a missing container
        # runtime / login here (same preflight the CLI uses), not as a
        # mid-run crash inside the pushed monitor.
        msg = sandbox_preflight(params.operator)
        if msg is not None:
            self.query_one("#f_err", Static).update(msg)
            return
        plan = prepare_evolve(params)
        cast("NetHackersApp", self.app).start_run(plan)  # background run + open its monitor
