"""Textual widget for the ``⚔ Evolve`` tab: a form that builds an
``EvolveParams`` from user input (owner/token defaulted from the logged-in
``Credentials``, mirroring what ``nethackers evolve`` derives from stored
creds) and hands it to ``harness.launch.prepare_evolve``. On success the
returned ``EvolvePlan`` drives a pushed ``EvolveScreen`` -- the same live
monitor the CLI path uses.

Objective is a **selection grid** (``tui.identity_grid.IdentityGrid``) that
mirrors the Frontier view: a grid of role cards, one ``◻``/``◼`` box per
variation. Space toggles the cursor cell (an identity, or a whole role via its
header); ``a`` selects all 73, ``c`` clears all; ←/→ hop between roles. The
grid resolves to ``EvolveParams.objective`` -- a single identity, a bare role,
``"*"`` (all), or a sorted comma-list -- every shape
``nethackers.hub.selector.resolve`` accepts. Seed root is a dropdown of the
solution roots discovered under ``roots/`` (dirs with a
``nethackers.solution.json``).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from nethackers.harness.discovery import CliInfo, ModelInfo, probe_operator
from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.models import EFFORTS, MODELS
from nethackers.harness.sandbox_preflight import (
    build_mutator_image,
    image_present,
    preflight as sandbox_preflight,
)
from nethackers.hub.selector import resolve
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.identity_grid import IdentityGrid

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

# The form doesn't expose an image picker, so live discovery probes the default
# mutator image (matches launch.EvolveParams.mutator_image / the CLI default).
_MUTATOR_IMAGE = "nethackers/mutator:latest"


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


def _version_line(backend: str, cli: CliInfo) -> str:
    """One muted status line for the detected operator CLI: version + auth
    state, or a clear 'not found' when the binary is missing from PATH."""
    if not cli.installed:
        return f"[#c04040]{backend} not found on PATH[/]"
    ver = cli.version or f"{backend} (version unknown)"
    if cli.logged_in is False:
        return f"[dim]{ver}[/] · [#c04040]not logged in[/]"
    return f"[dim]{ver}[/]"


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
    EvolveForm { layout: vertical; padding: 1 2; }
    EvolveForm Label { text-style: bold; color: #d2a24c; margin-top: 1; }
    /* two side-by-side subwindows over a full-width start bar */
    EvolveForm #f_panels { height: 1fr; }
    EvolveForm #f_objective {
        width: 2fr; border: round #7c745f; border-title-color: #d2a24c;
        border-title-align: left; padding: 0 1; margin-right: 1;
    }
    EvolveForm #f_operator {
        width: 1fr; border: round #7c745f; border-title-color: #d2a24c;
        border-title-align: left; padding: 0 1;
    }
    /* the grid is a .panel only so it's a nav stop -- suppress .panel's heavy
       border/padding (the subwindow frames it); a faint lift marks focus. */
    EvolveForm #f_obj_grid { height: auto; border: none; background: transparent; padding: 0; }
    EvolveForm #f_obj_grid:focus, EvolveForm #f_obj_grid.-cursor {
        border: none; background: #16160f;
    }
    EvolveForm #f_obj_sel { color: #d7c9a2; margin-top: 1; }
    EvolveForm #f_startbar { height: auto; margin-top: 1; }
    EvolveForm #f_start { width: auto; min-width: 18; }
    EvolveForm #f_err { width: 1fr; height: auto; color: #c04040; padding: 0 2; }
    EvolveForm #f_model_custom { display: none; }  /* shown only for Custom… */
    """

    def __init__(self, hub: str, creds: Credentials | None, **kw: Any) -> None:
        super().__init__(**kw)
        self._hub = hub
        self._creds = creds
        # The objective grid keeps the live selection; `_objective` is the token
        # it resolves to (updated on every IdentityGrid.Changed) -- the single
        # source of truth `_params()` reads.
        self._objective: str | None = None
        self._live: dict[str, ModelInfo] = {}  # id -> discovered model (drives efforts)
        # One ~1s container probe per operator, cached: switching operators back
        # and forth (or reopening) is then instant, not another probe.
        self._probe_cache: dict[str, tuple[CliInfo, list[ModelInfo] | None]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="f_panels"):
            # left subwindow: the objective selection grid
            with VerticalScroll(id="f_objective"):
                yield Label("↑↓←→ move · enter toggle · a all · c clear · esc leave")
                # classes="panel" makes it a modal-nav stop (app._nav_targets
                # collects focusable .panel widgets); its border is suppressed
                # below since the subwindow already frames it.
                yield IdentityGrid(id="f_obj_grid", classes="panel")
                yield Static("[dim]none selected[/]", id="f_obj_sel")
            # right subwindow: operator + model + effort + seed + iterations
            with VerticalScroll(id="f_operator"):
                yield Label("Operator")
                yield Select(
                    [("claude", "claude"), ("codex", "codex")],
                    value="claude", allow_blank=False, id="f_op",
                )
                yield Static("[dim]detecting…[/]", id="f_op_version")
                yield Label("Model")
                yield Select(self._model_options("claude"), value="",
                             allow_blank=False, id="f_model")
                yield Input(placeholder="custom model id…", id="f_model_custom")
                yield Label("Reasoning effort")
                yield Select([("Harness default", ""), *((e, e) for e in EFFORTS)],
                             value="", allow_blank=False, id="f_effort")
                yield Label("Seed root")
                roots = _seed_roots()
                yield Select(((r, r) for r in roots), value=roots[0],
                             allow_blank=False, id="f_seed")
                yield Label("Iterations")
                yield Input(value="1", id="f_iters")
        # full-width start bar below the two subwindows
        with Horizontal(id="f_startbar"):
            yield Button("Start", id="f_start", variant="success")
            yield Static("", id="f_err")

    def on_mount(self) -> None:
        # the two subwindows carry their own titles; their scroll panes are NOT
        # nav stops (their fields are), so blur them so a field never gets
        # shadowed by the whole-panel cursor. They still scroll via each
        # field's scroll_visible() as the cursor lands.
        obj = self.query_one("#f_objective")
        obj.border_title = "⚔ Objective"
        obj.can_focus = False
        op = self.query_one("#f_operator")
        op.border_title = "Operator & run"
        op.can_focus = False
        # No explicit _refresh_models("claude") kick here: #f_op is built with
        # a non-blank initial value, so Select's own _on_mount organically
        # fires one Select.Changed (-> on_select_changed's "f_op" branch calls
        # _refresh_models(backend)) all on its own. An extra explicit call
        # here would double-dispatch list_models -- two real Keychain+HTTP
        # round-trips per form mount (see
        # test_model_picker_dispatches_exactly_once_on_mount).

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

    def on_identity_grid_changed(self, event: IdentityGrid.Changed) -> None:
        """The objective grid changed -> recompute ``_objective`` (the token it
        resolves to) and the chip. ``_objective`` stays the single source of
        truth ``_params()`` reads."""
        self._objective = event.grid.token() or None
        chip = self.query_one("#f_obj_sel", Static)
        if not self._objective:
            chip.update("[dim]none selected[/]")
            return
        resolved = resolve(self._objective)
        if "," in self._objective:  # an arbitrary set -> show the count, not the long list
            chip.update(f"objective: [b]{len(resolved.identities)} builds[/] selected")
        else:  # a single identity, a role, or "*" (all)
            chip.update(
                f"objective: [b]{self._objective}[/] — {len(resolved.identities)} build(s)")

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
            self._set_effort_options("")             # reset efforts to the shared default
            self._maybe_refresh_models(str(event.value))   # refine from the live catalog
        elif event.select.id == "f_model":
            self.query_one("#f_model_custom", Input).display = event.value == "__custom__"
            self._set_effort_options(str(event.value))  # efforts follow the selected model

    def _maybe_refresh_models(self, backend: str) -> None:
        # Cache hit -> apply instantly on the UI thread (no docker). Miss -> the
        # off-thread probe below. Keeps operator switches snappy.
        cached = self._probe_cache.get(backend)
        if cached is not None:
            self._apply_discovery(backend, *cached)
        else:
            self._refresh_models(backend)

    @work(exclusive=True, thread=True)
    def _refresh_models(self, backend: str) -> None:
        # ONE container probe (probe_operator) runs the operator CLI INSIDE the
        # mutator image -- off the UI thread -- so version + the version-filtered
        # catalog match what a run actually uses, not the host's possibly-
        # different CLI. On a None catalog (image not built / offline / logged
        # out) the static list stays; the version line still reflects detection.
        cli, models = probe_operator(backend, image=_MUTATOR_IMAGE)
        self.app.call_from_thread(self._cache_and_apply, backend, cli, models)

    def _cache_and_apply(self, backend: str, cli: CliInfo,
                         models: list[ModelInfo] | None) -> None:
        self._probe_cache[backend] = (cli, models)
        self._apply_discovery(backend, cli, models)

    def _apply_discovery(self, backend: str, cli: CliInfo,
                         models: list[ModelInfo] | None) -> None:
        if str(self.query_one("#f_op", Select).value) != backend:
            return   # operator changed since this fetch started -- stale, drop it
        self.query_one("#f_op_version", Static).update(_version_line(backend, cli))
        if not models:
            return   # keep the static fallback
        sel = self.query_one("#f_model", Select)
        current = str(sel.value)
        options = [("Harness default", ""),
                   *[(m.label + (" (deprecated)" if m.deprecated else ""), m.id) for m in models],
                   ("Custom…", "__custom__")]
        self._live = {m.id: m for m in models}
        sel.set_options(options)
        valid = {m.id for m in models} | {"", "__custom__"}
        sel.value = current if current in valid else ""
        self._set_effort_options(str(sel.value))   # efforts now reflect the live model

    def _effort_options(self, model_id: str) -> list[tuple[str, str]]:
        # Efforts the selected model actually supports (from live discovery);
        # fall back to the shared static EFFORTS for an unknown / custom /
        # harness-default pick or when discovery gave no per-model reasoning.
        m = self._live.get(model_id)
        levels = list(m.reasoning) if (m and m.reasoning) else list(EFFORTS)
        return [("Harness default", ""), *((e, e) for e in levels)]

    def _set_effort_options(self, model_id: str) -> None:
        eff = self.query_one("#f_effort", Select)
        current = str(eff.value)
        options = self._effort_options(model_id)
        eff.set_options(options)
        valid = {v for _label, v in options}
        eff.value = current if current in valid else ""

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
            token=self._creds.access_token if self._creds else "dev-token",
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
        # The sandbox image is auto-provisioned: if it isn't built yet, build it
        # (off the UI thread, streaming progress into #f_err) and launch once
        # ready -- the user never runs `make`. Already built -> launch straight.
        if image_present(params.mutator_image):
            self._launch(params)
        else:
            self.query_one("#f_err", Static).update(
                "[yellow]setting up the sandbox[/] (first run — compiling NLE, a few minutes)…")
            self._build_then_launch(params)

    def _launch(self, params: EvolveParams) -> None:
        plan = prepare_evolve(params)
        cast("NetHackersApp", self.app).start_run(plan)  # background run + open its monitor

    @work(exclusive=True, thread=True)
    def _build_then_launch(self, params: EvolveParams) -> None:
        def _line(ln: str) -> None:
            self.app.call_from_thread(
                lambda: self.query_one("#f_err", Static).update(
                    f"[yellow]setting up the sandbox…[/] [dim]{ln}[/]"))
        err = build_mutator_image(params.mutator_image, on_line=_line)
        if err is not None:
            self.app.call_from_thread(
                lambda: self.query_one("#f_err", Static).update(err))
            return
        self.app.call_from_thread(self._launch, params)
