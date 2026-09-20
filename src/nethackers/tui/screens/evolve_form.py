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
``nethackers.hub.selector.resolve`` accepts. The parent to evolve always
comes from the hub's top trusted elite (SELECT); the local seed under
``roots/`` is only the bootstrap fallback used before any elite exists, so it
is not a form field -- the CLI's ``--seed``/``--from-seed`` cover the expert
cold-start case.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from nethackers.config import OFFLINE_OWNER, OFFLINE_TOKEN, load_stage
from nethackers.containers import container_runtime
from nethackers.harness.discovery import CliInfo, ModelInfo, probe_operator
from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.models import EFFORTS, MODELS
from nethackers.harness.sandbox_preflight import (
    ensure_image,
    image_present,
    preflight as sandbox_preflight,
    resolve_image,
    sandbox_platform_mismatch,
)
from nethackers.hub.ids import AUTOASCEND_TREE
from nethackers.hub.selector import resolve
from nethackers.hubclient.credentials import Credentials
from nethackers.hubclient.publish import gh_state
from nethackers.operators import DEFAULT_OPERATOR, OPERATORS
from nethackers.solution_root import resolve_solution_root
from nethackers.tui.identity_grid import IdentityGrid
from nethackers.tui.status import _bar

if TYPE_CHECKING:
    from nethackers.diagnostics import CheckResult
    from nethackers.harness.pull_events import PullEvent
    from nethackers.tui.app import NetHackersApp


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
    if found:
        return found
    # Nothing under ./roots -- off a checkout, that is every pip user. Offer
    # the tree that actually exists (the wheel's own copy) rather than the
    # canonical path we just failed to find.
    autoascend = resolve_solution_root(AUTOASCEND_TREE)
    return [autoascend.as_posix() if autoascend.is_dir() else AUTOASCEND_TREE]


def _publish_warning(owner: str) -> str:
    """The Start-time publish-readiness note, mirroring the CLI's three
    branches VERBATIM (``cli.py:823-842``) so both surfaces read identically.
    A hub-logged-in but `gh`-unauthed contestant would otherwise evolve, win,
    and have the win silently stay local (spec 5.6) -- warn up front instead.
    Advisory only: the caller writes this into ``#f_publish_warn`` and
    launches the run regardless of what comes back (including ``""``, the
    all-clear "authed" case) -- a local elite is kept either way.

    The form has no ``--offline`` flag (it's interactive), so the CLI's
    ``not args.offline`` guard collapses to just the owner check here."""
    if owner == OFFLINE_OWNER:
        return ("[dim]not logged in — running offline "
                "(publishing needs `nethackers login`)[/]")
    _gh_login, state = gh_state()
    if state == "missing":
        return ("[yellow]wins won't publish[/] — install the GitHub CLI "
                "(`gh`), then run `gh auth login`")
    if state == "unauthed":
        return ("[yellow]wins won't publish[/] — run `gh auth login` "
                "(separate from `nethackers login`)")
    return ""  # authed -- nothing to warn about


def _version_line(backend: str, cli: CliInfo) -> str:
    """One muted status line for the detected operator CLI: version + auth
    state, or a clear 'not found' when the binary is missing from PATH."""
    if not cli.installed:
        return f"[#c04040]{backend} not found on PATH[/]"
    ver = cli.version or f"{backend} (version unknown)"
    if cli.logged_in is False and backend == "opencode2":
        # No provider key in the global opencode.json: runs still work, on
        # OpenCode's free models only.
        return f"[dim]{ver} · free models only[/]"
    if cli.logged_in is False:
        return f"[dim]{ver}[/] · [#c04040]not logged in[/]"
    return f"[dim]{ver}[/]"


# `probe_operator` returns `installed=False` ONLY when the mutator image itself
# is absent (discovery.py: `if not image_present(image): return CliInfo(backend,
# False, None, None), None`) -- the container that would run the backend can't
# even start, so the curated static model list is exactly as unverifiable as a
# live catalog would be. Name that in the picker instead of a silently generic
# "Harness default" (spec 5.8). A value DISTINCT from "" is load-bearing, not
# cosmetic: Select.value is a plain reactive that only redraws the visible
# SelectCurrent label on an actual value change, and the static fallback this
# replaces already leaves the picker at value=="" -- reassigning that same ""
# would silently leave the stale "Harness default" text on screen even though
# the option list underneath had changed. `_model()` maps this back onto "no
# explicit pin", same as "".
_NO_SANDBOX_MODEL_VALUE = "__no_sandbox__"
_NO_SANDBOX_MODEL_LABEL = "pull the sandbox to see models"


class EvolveForm(Vertical):
    """The ``⚔ Evolve`` tab: filter-and-pick an objective and operator, set
    iterations, and **Start** -- which builds an ``EvolveParams``, calls
    ``prepare_evolve``, and pushes the live ``EvolveScreen`` monitor over
    the dashboard.

    A focused text ``Input`` (the objective filter, iterations, budget)
    swallows printable keys, so the shell's advertised ``q`` quit -- and the
    ``1``–``6`` switches -- go dead while you're typing in one. ``escape``
    (``action_leave_field``) hands focus back to the main nav so those global
    keys work again: the way out of a field a stuck user reaches for."""

    BINDINGS = [Binding("escape", "leave_field", "Back to menu", show=False)]

    DEFAULT_CSS = """
    EvolveForm { layout: vertical; padding: 1 2; }
    EvolveForm Label { text-style: bold; color: #d2a24c; margin-top: 1; }
    /* readiness strip: a full-width row above the two subwindows -- "what
       evolve needs" is the first thing you see (spec 5.8). */
    EvolveForm #f_readiness {
        height: auto; border: round #7c745f; border-title-color: #d2a24c;
        border-title-align: left; padding: 0 1; margin-bottom: 1;
    }
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
    /* pull-progress surface (Task 5, spec 5.5/5.8): phase-driven text from the
       provisioning worker, updated via call_from_thread since the worker is
       off the UI thread -- image + short digest + "one-time pull" + a layers
       m/n meter. #f_err is reserved for genuine errors only now; this carries
       progress instead. Own full-width row, no fixed color -- dim/green come
       entirely from inline markup. */
    EvolveForm #f_pull { height: auto; padding: 0 2; margin-top: 1; }
    /* persists through the run -- own full-width row, no fixed color: its
       three states (dim/yellow/none) come entirely from inline markup. */
    EvolveForm #f_publish_warn { height: auto; padding: 0 2; margin-top: 1; }
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
        yield Static("[dim]checking readiness…[/]", id="f_readiness")
        with Horizontal(id="f_panels"):
            # left subwindow: the objective selection grid
            with VerticalScroll(id="f_objective"):
                yield Label("↑↓←→ move · enter toggle · a all · c clear · esc leave")
                # classes="panel" makes it a modal-nav stop (app._nav_targets
                # collects focusable .panel widgets); its border is suppressed
                # below since the subwindow already frames it.
                yield IdentityGrid(id="f_obj_grid", classes="panel")
                yield Static("[dim]none selected[/]", id="f_obj_sel")
            # right subwindow: operator + model + effort + iterations
            with VerticalScroll(id="f_operator"):
                yield Label("Operator")
                yield Select(
                    [(op, op) for op in OPERATORS],
                    value=DEFAULT_OPERATOR, allow_blank=False, id="f_op",
                )
                yield Static("[dim]detecting…[/]", id="f_op_version")
                yield Label("Model")
                yield Select(self._model_options(DEFAULT_OPERATOR), value="",
                             allow_blank=False, id="f_model")
                yield Input(placeholder="custom model id…", id="f_model_custom")
                yield Label("Reasoning effort")
                yield Select([("Harness default", ""), *((e, e) for e in EFFORTS)],
                             value="", allow_blank=False, id="f_effort")
                yield Label("Iterations")
                yield Input(value="1", id="f_iters")
                yield Label("Network")
                yield Select(
                    [("Fast — self-reported (default)", "self-reported"),
                     ("Verified — trusted scores on hidden seeds", "verified")],
                    value="self-reported", allow_blank=False, id="f_network",
                )
                yield Static(
                    "[dim]Fast pulls from the open leaderboard — scores are self-reported "
                    "claims, and it's safe to run because every program runs in the sealed "
                    "sandbox. Verified pulls only programs re-scored on hidden seeds, so the "
                    "score is trustworthy. This changes which programs you pull, never how "
                    "safely they run.[/]",
                    id="f_network_help",
                )
        # full-width start bar below the two subwindows
        with Horizontal(id="f_startbar"):
            yield Button("Start", id="f_start", variant="success")
            yield Static("", id="f_err")
        # provisioning's typed pull-progress surface (Task 5) -- own row, own
        # id, separate from #f_err (genuine errors only) so a mid-pull layer
        # count is never mistaken for a failure -- see _apply_pull.
        yield Static("", id="f_pull")
        # separate from #f_err/#f_pull (own row, own id) so a publish warning
        # survives past Start -- see _publish_warning.
        yield Static("", id="f_publish_warn")

    def on_mount(self) -> None:
        self.query_one("#f_readiness", Static).border_title = "What evolve needs"
        self._refresh_readiness()

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

        # Plain-language tips on every field (hover to read) so the form is
        # legible without knowing the evolve internals.
        tips = {
            "#f_obj_grid": "The NetHack character(s) to evolve a bot for. Pick one "
                           "identity, a whole role, or several — the bot is scored on "
                           "every one you select.",
            "#f_op": ("The coding agent that rewrites the bot each round "
                      "(Claude, Codex, or OpenCode 2)."),
            "#f_model": "Which model that agent uses. 'Harness default' lets it choose.",
            "#f_model_custom": "Type an exact model id the picker doesn't list.",
            "#f_effort": "How hard the model thinks per change: higher = smarter but "
                         "slower and costlier.",
            "#f_iters": "Improvement rounds to run. Each picks a random build from the "
                        "objective and mutates its current best.",
        }
        for sel, tip in tips.items():
            self.query_one(sel).tooltip = tip

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
            self._live = {}  # never carry another operator's effort metadata across
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

    @work(exclusive=True, thread=True, exit_on_error=False)
    def _refresh_models(self, backend: str) -> None:
        # ONE container probe (probe_operator) runs the operator CLI INSIDE the
        # mutator image -- off the UI thread -- so version + the version-filtered
        # catalog match what a run actually uses, not the host's possibly-
        # different CLI. On a None catalog (image not built / offline / logged
        # out) the static list stays; the version line still reflects detection.
        cli, models = probe_operator(
            backend, image=resolve_image(load_stage().mutator_image, "mutator"),
            docker=container_runtime() or "docker")  # docker OR podman -- issue #50
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
        if not cli.installed:
            # The mutator image is absent -- there is no live catalog AND the
            # curated static list is exactly as unverifiable, so say why
            # instead of quietly offering "Harness default" (spec 5.8).
            sel = self.query_one("#f_model", Select)
            sel.set_options([(_NO_SANDBOX_MODEL_LABEL, _NO_SANDBOX_MODEL_VALUE),
                             ("Custom…", "__custom__")])
            sel.value = _NO_SANDBOX_MODEL_VALUE
            return
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

    @work(exclusive=True, thread=True, exit_on_error=False)
    def _refresh_readiness(self) -> None:
        # The strip reports readiness for ALL registered coding agents
        # (run_checks' `operator=None` default), not the currently-picked one:
        # evolve drives one operator chosen at Start, so "what evolve needs" is
        # "at least one agent logged in", and the operator row lists each. No
        # widget is read here, so nothing needs capturing off the UI thread.
        # `manifest_reachable=lambda ref: False` is load-bearing: the image
        # checks NEVER make a GHCR round-trip just because the user opened this
        # tab (spec 5.8) -- local `docker image inspect` only. `only=evolve_ids`
        # stops run_checks from running the other 3 checks (hub/hub_login/gh) AT
        # ALL -- those touch a hub HTTPS call, a creds read, and a `gh`
        # subprocess, and `_apply_readiness` only displays evolve-tagged rows.
        # Off the UI thread because run_checks shells out (docker, host login
        # probes).
        from nethackers import diagnostics
        evolve_ids = [cid for cid, (_sev, caps) in diagnostics.CHECK_SPECS.items()
                     if "evolve" in caps]
        results = diagnostics.run_checks(
            manifest_reachable=lambda ref: False,
            only=evolve_ids,
        )
        self.app.call_from_thread(self._apply_readiness, results)

    def _apply_readiness(self, results: list[CheckResult]) -> None:
        from nethackers import diagnostics
        glyphs = {"ok": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
        rows = [r for r in results if "evolve" in r.capabilities]
        lines: list[str] = []
        for r in rows:
            if r.items:  # a check with a per-item breakdown (operator) -> sublist
                lines.append(f"{glyphs[r.status]} {r.id}")
                for it in r.items:
                    lines.append(f"    {glyphs[it.status]} {it.label}: {it.detail}")
            else:
                lines.append(f"{glyphs[r.status]} {r.id}: {diagnostics._short_digest(r.detail)}")
        ready = diagnostics.capability_ready(results, "evolve")
        verdict = ("[green]ready to evolve[/]" if ready
                  else "[yellow]evolve not ready — see above[/]")
        self.query_one("#f_readiness", Static).update("\n".join(lines + [verdict]))

    def _effort_options(self, model_id: str) -> list[tuple[str, str]]:
        # Efforts the selected model actually supports (from live discovery);
        # fall back to the shared static EFFORTS for an unknown / custom /
        # harness-default pick or when discovery gave no per-model metadata.
        # A known-empty set is different: OpenCode models such as Big Pickle
        # have no variants, so offering "medium" creates a provider.no-route
        # error instead of changing reasoning effort. And OpenCode 2 applies
        # effort only as a variant of a named model, so with no model pinned
        # there is nothing to apply it to.
        if (str(self.query_one("#f_op", Select).value) == "opencode2"
                and model_id in ("", _NO_SANDBOX_MODEL_VALUE)):
            return [("Harness default", "")]
        m = self._live.get(model_id)
        levels = (list(m.reasoning) if m and (m.reasoning or m.reasoning_known)
                  else list(EFFORTS))
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
        if value == _NO_SANDBOX_MODEL_VALUE:
            return None   # no sandbox to verify against -- same as unpinned
        return value or None  # "" (harness default) -> None

    def _effort(self) -> str | None:
        return str(self.query_one("#f_effort", Select).value) or None

    def _network_tier(self) -> str:
        return str(self.query_one("#f_network", Select).value)

    def _params(self) -> EvolveParams:
        if not self._objective:
            raise ValueError("pick an objective from the list")
        try:
            iters = int(self.query_one("#f_iters", Input).value)
        except ValueError:
            raise ValueError("iterations must be an integer") from None
        return EvolveParams(
            objective=self._objective,
            # Parent comes from the hub SELECT; the seed is only the cold-start
            # fallback before any elite exists -- default to the discovered root
            # (roots/autoascend) instead of a form field.
            seed=_seed_roots()[0],
            operator=str(self.query_one("#f_op", Select).value),
            iterations=iters,
            model=self._model(),
            effort=self._effort(),
            hub=self._hub,
            token=self._creds.access_token if self._creds else OFFLINE_TOKEN,
            owner=self._creds.login if self._creds else OFFLINE_OWNER,
            tier=self._network_tier(),
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
        # Publish-readiness pre-check (spec 5.6): advisory only -- it NEVER
        # gates the launch below. Written into its own #f_publish_warn (not
        # #f_err) so it's still on screen after Start, through provisioning
        # and the run -- a win is kept as a local elite either way.
        self.query_one("#f_publish_warn", Static).update(_publish_warning(params.owner))
        # Both sandbox images are auto-provisioned: if either isn't built/
        # pulled yet, acquire it (off the UI thread, streaming typed progress
        # into #f_pull -- see _apply_pull) and launch once ready -- the user
        # never runs `make`/`docker pull`. The arena image needs this exactly
        # as much as the mutator: run_loop scores every iteration through it
        # (harness/evaluate.py), so provisioning it only here -- not mid-loop
        # -- keeps a missing/stale arena image a fail-fast Start-time error
        # instead of a confusing mid-run stall. Already present -> launch
        # straight.
        # Resolve the container runtime once (docker OR podman -- issue #50);
        # sandbox_preflight above already confirmed one is usable, so this is
        # non-None. Threads into the presence checks, the provision worker AND
        # `params.runtime` so the whole Start path -- the launched RUN included
        # -- uses the same detected binary. That last one is what issue #54 was:
        # `EvolveParams.runtime` defaults to "docker" and only the CLI evolve
        # handler used to override it, so a podman-only host passed every check
        # here and then launched a run whose `ContainerOperator` and arena evals
        # (both read `params.runtime`, see harness/launch.py) exec'd a `docker`
        # that isn't installed.
        runtime = container_runtime() or "docker"
        params.runtime = runtime
        if image_present(params.mutator_image, runtime=runtime) and \
                image_present(params.image, runtime=runtime):
            self._launch(params, runtime)
        else:
            self.query_one("#f_pull", Static).update(
                "[yellow]setting up the sandbox[/] (first run — a few minutes)…")
            self._provision_then_launch(params, runtime)

    def _launch(self, params: EvolveParams, runtime: str) -> None:
        # Both images are present by now, on either path here. The agent scores
        # its own candidates inside the mutator: on another platform than the
        # arena it would optimize games the arena never plays.
        msg = sandbox_platform_mismatch(params.image, params.mutator_image, runtime=runtime)
        if msg is not None:
            self.query_one("#f_err", Static).update(Text.from_markup(msg))
            return
        plan = prepare_evolve(params)
        cast("NetHackersApp", self.app).start_run(plan)  # background run + open its monitor

    @work(exclusive=True, thread=True)
    def _provision_then_launch(self, params: EvolveParams, runtime: str = "docker") -> None:
        """Acquire whichever sandbox image(s) Start found missing, then
        launch. Runs off the UI thread (``@work(thread=True)``) -- every
        widget touch below is marshaled back onto it via ``call_from_thread``.
        ``ensure_image``'s ``on_event`` callback fires from THIS worker
        thread (it's called synchronously inside ``ensure_image``, which we
        called), never the UI thread, so it must never touch ``#f_pull``
        directly -- only ever through ``_apply_pull`` via ``call_from_thread``
        (spec S5.5/5.8's typed pull-progress seam, replacing the old raw
        ``on_line`` text dump into ``#f_err``)."""
        for ref, kind in ((params.mutator_image, "mutator"), (params.image, "arena")):
            err = ensure_image(
                ref, kind, runtime=runtime,
                on_event=lambda e: self.app.call_from_thread(self._apply_pull, e))
            if err is not None:
                # Rich markup, with any raw build lines in it escaped for Rich: read it
                # with Rich as the CLI does, since Textual's own parser takes some of
                # those brackets (`[ 45%]`, `[Warning]`) for tags.
                self.app.call_from_thread(
                    lambda e=err: self.query_one("#f_err", Static).update(Text.from_markup(e)))
                return
        self.app.call_from_thread(self._launch, params, runtime)

    def _apply_pull(self, event: PullEvent) -> None:
        """Phase-driven ``#f_pull`` update from one ``PullEvent`` -- always
        invoked on the UI thread via ``call_from_thread`` (see
        ``_provision_then_launch``), never called directly from the worker.

        Consent framing (spec 5.8/5.5): the Start click IS the consent to
        pull, so ``"start"`` discloses exactly what's being acquired --
        image + short digest + "one-time pull". NO size/GB anywhere (deferred
        by ruling, not merely unimplemented -- never add one here).
        ``"layer"`` shows a layers m/n meter; guarded for the ``make``-build
        path, which has no layer concept at all (``layers_total`` is always
        ``None`` there), so those events just leave "start"'s text standing.
        ``"done"`` is a ✓ line for that kind. ``"error"`` goes to ``#f_err``
        -- the genuine-error surface -- instead of here: ``#f_pull`` is
        progress-only, and ``_provision_then_launch`` writes the real,
        one-command-fix error text (``ensure_image``'s return value) into
        ``#f_err`` right after this fires, superseding whatever's written
        below."""
        from nethackers import diagnostics
        short_ref = diagnostics._short_digest(event.ref)
        if event.phase == "start":
            self.query_one("#f_pull", Static).update(
                f"[dim]pulling {event.kind}  {short_ref}  — one-time pull[/]")
        elif event.phase == "layer":
            if event.layers_total is None or event.layers_complete is None:
                return  # the make-build path has no layers -- "start"'s text stands
            frac = event.layers_complete / event.layers_total
            self.query_one("#f_pull", Static).update(
                f"[dim]pulling {event.kind}  {_bar(frac)}  "
                f"{event.layers_complete}/{event.layers_total} layers[/]")
        elif event.phase == "done":
            self.query_one("#f_pull", Static).update(
                f"[green]✓ {event.kind} sandbox ready[/]  {short_ref}")
        elif event.phase == "error":
            # raw build/pull output: shown as written, never parsed as markup
            self.query_one("#f_err", Static).update(Text(event.detail or "pull failed",
                                                         style="red"))
