"""Textual widget for the ``⚔ Evolve`` tab: a form that builds an
``EvolveParams`` from user input (owner/token defaulted from the logged-in
``Credentials``, mirroring what ``nethackers evolve`` derives from stored
creds) and hands it to ``harness.launch.prepare_evolve``. On success the
returned ``EvolvePlan`` drives a pushed ``EvolveScreen`` -- the same live
monitor the CLI path uses.

Objective is a **filter + multi-pick** over the catalog (74 entries:
``random`` plus the 73 identities; ``all`` is excluded -- it's a hub-query
marker with no episodes to evolve against), so you type a fragment
(``wiz``, ``val``) and choose from the narrowed list instead of typing an
exact ``role-race-align-gender`` string. A filter that names a whole role
(e.g. ``wiz``) also offers a "whole role" option; picking several entries
(roles and/or identities) builds a set. Either way ``EvolveParams.objective``
ends up a single identity, a bare role, or a sorted comma-list of
identities -- every shape ``nethackers.hub.selector.resolve`` accepts. Seed
root is a dropdown of the solution roots discovered under ``roots/`` (dirs
with a ``nethackers.solution.json``).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from nethackers.harness.discovery import CliInfo, ModelInfo, probe_operator
from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.models import EFFORTS, MODELS
from nethackers.harness.sandbox_preflight import (
    build_mutator_image,
    image_present,
    preflight as sandbox_preflight,
)
from nethackers.hub.objectives import CATALOG, ROLES
from nethackers.hub.selector import resolve
from nethackers.hubclient.credentials import Credentials

if TYPE_CHECKING:
    from nethackers.tui.app import NetHackersApp

# random first (the north-star), then the identities sorted; drop "all".
_OBJECTIVES: list[str] = ["random"] + sorted(k for k in CATALOG if k not in ("random", "all"))

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
        # Ordered multi-selection of OptionList ids: a plain identity id
        # (e.g. "wiz-elf-cha-mal") or a role id ("role:wiz"). _objective is
        # always kept in sync (via _toggle_selection) as _selector()'s
        # rendering of this set -- the single source of truth _params() reads.
        self._selected: list[str] = []
        self._live: dict[str, ModelInfo] = {}  # id -> discovered model (drives efforts)
        # One ~1s container probe per operator, cached: switching operators back
        # and forth (or reopening) is then instant, not another probe.
        self._probe_cache: dict[str, tuple[CliInfo, list[ModelInfo] | None]] = {}

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
            yield Static("[dim]detecting…[/]", id="f_op_version")
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

    def on_input_changed(self, event: Input.Changed) -> None:
        """Narrow the objective list as the filter is typed. A query that
        names a whole role (e.g. "wiz") gets a "whole role" option prepended
        (id ``role:<role>``) so one pick selects every identity in it."""
        if event.input.id != "f_obj_filter":
            return
        q = event.value.strip().lower()
        matches = [o for o in _OBJECTIVES if q in o.lower()]
        row_options = [Option(o, id=o) for o in matches]
        if q in ROLES:
            count = len(resolve(q).identities)
            row_options.insert(0, Option(f"{q} — whole role ({count})", id=f"role:{q}"))
        options = self.query_one("#f_obj_list", OptionList)
        options.clear_options()
        options.add_options(row_options)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is None:
            return
        self._toggle_selection(option_id)
        chip = self.query_one("#f_obj_sel", Static)
        if self._objective:
            resolved = resolve(self._objective)
            if resolved.kind in ("random", "all"):
                # a broad marker, not a discrete identity list -- no "N
                # build(s)" count to show (random: 0 by design; all: every
                # identity, not what the user picked one-by-one).
                chip.update(f"objective: [b]{self._objective}[/]")
            else:
                chip.update(
                    f"objective: [b]{self._objective}[/] — {len(resolved.identities)} build(s)")
        else:
            chip.update("[dim]none selected[/]")

    # "random"/"all" are broad, non-set catalog markers -- they can't be
    # unioned with identities/roles, so they're mutually exclusive with
    # everything else in _selected (see _toggle_selection).
    _BROAD = ("random", "all")

    def _toggle_selection(self, option_id: str) -> None:
        """Toggle *option_id* (a plain identity id, a ``role:<role>`` id, or
        a broad marker -- ``"random"``/``"all"``) in/out of the ordered
        multi-selection, then recompute ``_objective`` from the result via
        ``_selector()``. Pure state -- no widget queries -- so it runs
        standalone in a unit test without a mount.

        ``"random"``/``"all"`` can't be unioned with anything else
        (``resolve("random").identities`` is empty, so mixing it in would
        silently vanish that intent) -- picking one REPLACES the whole
        selection (or clears it, toggling off an already-sole pick);
        picking an identity/role drops any broad marker first."""
        if option_id in self._BROAD:
            self._selected = [] if self._selected == [option_id] else [option_id]
        else:
            self._selected = [s for s in self._selected if s not in self._BROAD]
            if option_id in self._selected:
                self._selected.remove(option_id)
            else:
                self._selected.append(option_id)
        self._objective = self._selector() or None

    def _selector(self) -> str:
        """Render ``self._selected`` to a token ``selector.resolve`` accepts:
        ``""`` when empty, the bare role/identity/broad-marker for a single
        pick, else a sorted, deduped comma-list of the union of every
        selected identity (expanding any role pick to its members first)."""
        if not self._selected:
            return ""
        if len(self._selected) == 1:
            return self._selected[0].removeprefix("role:")
        identities: set[str] = set()
        for option_id in self._selected:
            identities.update(resolve(option_id.removeprefix("role:")).identities)
        return ",".join(sorted(identities))

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
