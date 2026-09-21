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
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Select, Static

import nethackers.tui.screens.evolve_form as ef
from nethackers import diagnostics
from nethackers.harness.discovery import CliInfo, ModelInfo
from nethackers.harness.pull_events import PullEvent
from nethackers.hubclient.credentials import Credentials
from nethackers.tui.app import NetHackersApp
from nethackers.tui.identity_grid import IdentityGrid
from nethackers.tui.screens.evolve_form import EvolveForm


@pytest.fixture(autouse=True)
def _no_live_models(monkeypatch):
    # Default: discovery detects the CLI but returns no catalog -> the form keeps
    # its static fallback, so every existing test sees today's behavior (and no
    # real `docker run` probe). One probe_operator call fetches both.
    monkeypatch.setattr(
        ef, "probe_operator",
        lambda backend, **k: (CliInfo(backend, True, f"{backend} 9.9.9", True), None))


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

    @staticmethod
    def run(cb=None):
        return []


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


async def test_every_field_has_a_plain_language_tooltip():
    """Each field carries a tip so a layman can read what it does on hover."""
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        for sel in ("#f_obj_grid", "#f_op", "#f_model", "#f_effort", "#f_iters"):
            assert form.query_one(sel).tooltip, f"{sel} has no tooltip"


async def test_form_scroll_pane_is_not_a_nav_target():
    # regression: a subwindow VerticalScroll must scroll without itself being a
    # focusable nav stop that shadows its fields (the operator select became
    # unreachable/"not selectable" when the whole pane grabbed the cursor).
    app = NetHackersApp(hub="http://h", creds=None, start="evolve")
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        await pilot.pause()
        targets = app._nav_targets()
        assert app.query_one("#f_objective") not in targets    # subwindow panes aren't stops
        assert app.query_one("#f_operator") not in targets
        assert app.query_one("#f_op", Select) in targets       # but the fields are
        assert app.query_one("#f_model", Select) in targets
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


def _refuse_mismatched_platforms(monkeypatch, acquired=()):
    """Fake the sandbox platform guard into refusing. Returns one record per
    call: the runtime it would inspect with, and the image kinds ``acquired``
    held by then -- a guard that can't read a platform passes, so both matter."""
    calls: list = []

    def _mismatch(arena, mutator, **k):
        calls.append({"runtime": k.get("runtime"), "acquired": sorted(acquired)})
        return "[red]sandbox platform mismatch[/] — rebuild the mutator image"
    monkeypatch.setattr(ef, "sandbox_platform_mismatch", _mismatch)
    return calls


async def test_platform_mismatch_shows_error_no_start(monkeypatch):
    """Sandboxes on different platforms would have the agent optimizing games
    the arena never plays: Start refuses, in #f_err, as the CLI does."""
    seen: dict = {}
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: seen.update(called=True))
    monkeypatch.setattr(ef, "container_runtime", lambda **k: "podman")
    calls = _refuse_mismatched_platforms(monkeypatch)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert "called" not in seen         # prepare_evolve blocked by the guard
        assert app.started is None
        assert calls == [{"runtime": "podman", "acquired": []}]
        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "sandbox platform mismatch" in err_text


async def test_platform_mismatch_after_provisioning_shows_error_no_start(monkeypatch):
    """A fresh machine's first Start pulls both images, then launches from the
    worker: that path is the one most likely to meet a mismatch, so it is
    guarded too -- after both images are acquired, with the same runtime."""
    seen: dict = {}
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: seen.update(called=True))
    monkeypatch.setattr(ef, "container_runtime", lambda **k: "podman")
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)   # provision first
    acquired: list = []
    monkeypatch.setattr(ef, "ensure_image",
                        lambda ref, kind, **k: acquired.append(kind) or None)
    calls = _refuse_mismatched_platforms(monkeypatch, acquired)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()   # the provision-then-launch worker
        await pilot.pause()
        assert "called" not in seen
        assert app.started is None
        assert calls == [{"runtime": "podman", "acquired": ["arena", "mutator"]}]
        err_text = str(app.query_one("#f_err", Static).render()).lower()
        assert "sandbox platform mismatch" in err_text


async def test_model_picker_populates_from_live_discovery(monkeypatch):
    monkeypatch.setattr(
        ef, "probe_operator",
        lambda backend, **k: (
            CliInfo(backend, True, f"{backend} x", True),
            [ModelInfo("live-sol-9", "Live Sol 9")] if backend == "claude" else None,
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
    # kick from on_mount would double-dispatch probe_operator -- two real
    # `docker run` probes per form mount in production. Lock it at one.
    calls: list[str] = []

    def _counting_probe(backend, **k):
        calls.append(backend)
        return CliInfo(backend, True, f"{backend} x", True), None

    monkeypatch.setattr(ef, "probe_operator", _counting_probe)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == ["claude"]        # exactly one dispatch on mount, not two


async def test_operator_version_line_shows_detected_cli(monkeypatch):
    # The form surfaces the detected operator CLI's version (spec 3C) via the
    # same single discovery worker that populates the model list.
    monkeypatch.setattr(ef, "probe_operator",
                        lambda backend, **k: (CliInfo(backend, True, "claude 2.1.237", True), None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        line = str(app.query_one("#f_op_version", Static).render())
        assert "2.1.237" in line          # detected version shown for the default operator


async def test_operator_version_line_shows_not_found_when_missing(monkeypatch):
    monkeypatch.setattr(ef, "probe_operator",
                        lambda backend, **k: (CliInfo(backend, False, None, None), None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        line = str(app.query_one("#f_op_version", Static).render()).lower()
        assert "not found" in line         # missing binary flagged, not a crash


async def test_effort_options_follow_selected_model(monkeypatch):
    # The Reasoning-effort picker is driven by the selected model's discovered
    # efforts (not a hardcoded list). Unknown metadata falls back to the shared
    # list, while a catalog-confirmed empty set must remain default-only.
    monkeypatch.setattr(
        ef, "probe_operator",
        lambda backend, **k: (
            CliInfo(backend, True, f"{backend} x", True),
            [ModelInfo("m-rich", "Rich", ("low", "high", "ultra"), False),
             ModelInfo("m-bare", "Bare", (), False),
             ModelInfo("m-no-variants", "No variants", (), False, True)]
            if backend == "claude" else None,
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
        assert form._effort_options("m-no-variants") == [
            ("Harness default", "")]
        form.query_one("#f_model", Select).value = "m-rich"
        await pilot.pause()
        eff = form.query_one("#f_effort", Select)
        eff.value = "ultra"                # a live-only level absent from static EFFORTS
        assert eff.value == "ultra"        # picker was repopulated from live reasoning


async def _switch_operator(app, pilot, backend: str):
    form = app.query_one(ef.EvolveForm)
    form.query_one("#f_op", Select).value = backend
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return form


async def test_opencode2_offers_no_effort_without_a_pinned_model(monkeypatch):
    # OpenCode 2 applies effort only as `provider/model#variant`: under
    # "Harness default" a picked effort was silently dropped from the run.
    monkeypatch.setattr(ef, "probe_operator", lambda backend, **k: (
        CliInfo(backend, True, f"{backend} x", True), None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        assert len(form._effort_options("")) > 1            # claude applies it model-less
        form = await _switch_operator(app, pilot, "opencode2")
        assert form._effort_options("") == [("Harness default", "")]
        assert form._effort_options("__custom__") == [
            ("Harness default", ""), *((e, e) for e in ef.EFFORTS)]


async def test_opencode2_version_line_names_free_models_without_a_key(monkeypatch):
    keyed = {"value": False}
    monkeypatch.setattr(ef, "probe_operator", lambda backend, **k: (
        CliInfo(backend, True, "1.18.31", keyed["value"]), None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await _switch_operator(app, pilot, "opencode2")
        line = str(app.query_one("#f_op_version", Static).render())
        assert "free models only" in line

    keyed["value"] = True
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await _switch_operator(app, pilot, "opencode2")
        line = str(app.query_one("#f_op_version", Static).render())
        assert "free models only" not in line and "1.18.31" in line


async def test_missing_image_builds_then_launches(monkeypatch):
    """When a sandbox image isn't built/pulled, Start acquires it (off the UI
    thread, streaming typed progress into #f_pull -- see
    test_provisioning_renders_typed_pull_progress_not_raw_text for that
    surface's own coverage) and launches once ready -- the user never runs
    `make`/`docker pull`. Both images (mutator + arena) go through this."""
    seen: dict = {}

    def _fake_prepare(p, **k):
        seen["prepared"] = True
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare)
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)   # not built -> build path
    provisioned: list[tuple[str, str]] = []

    def _ensure(ref, kind, on_event=None, **k):
        provisioned.append((ref, kind))
        if on_event:
            on_event(PullEvent(kind=kind, ref=ref, phase="done",
                               layers_total=None, layers_complete=None, detail=""))
        return None                        # success

    monkeypatch.setattr(ef, "ensure_image", _ensure)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()   # the provision-then-launch worker
        await pilot.pause()
        kinds = {kind for _ref, kind in provisioned}
        assert kinds == {"mutator", "arena"}      # both images were auto-provisioned
        assert seen.get("prepared") is True       # prepare_evolve ran after provisioning
        assert isinstance(app.started, _Plan)     # and the plan reached start_run


# ---------------------------------------------------------------------------
# Typed pull-progress surface (Task 5, spec 5.5/5.8): #f_pull replaces the
# raw `docker pull` text that used to be dumped into #f_err during
# provisioning. Start is the consent to pull -- it discloses image + short
# digest + "one-time pull" + a layers m/n meter. Size/GB is deferred by
# ruling and must never appear. `ensure_image` is faked at `ef.ensure_image`
# -- the exact name `_provision_then_launch` calls (a bare global lookup at
# call time, not a bound default captured at def-time -- the "bound-default
# trap" to watch for), so patching it here is the seam the form actually
# dereferences; `provisioned`/`calls` below confirm the fake was really hit.
# ---------------------------------------------------------------------------

async def test_provisioning_renders_typed_pull_progress_not_raw_text(monkeypatch):
    """Drive `_provision_then_launch` with `ensure_image` faked to emit a
    scripted start -> layer -> layer -> done sequence for a realistic
    long-digest ref, and check what `#f_pull` shows at each phase (captured
    via a spy on the real `_apply_pull`, called -- like production -- only on
    the UI thread via `call_from_thread`, so reading the widget from inside
    the spy is safe)."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)

    long_ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "a" * 64
    scripted = [
        PullEvent(kind="mutator", ref=long_ref, phase="start",
                  layers_total=None, layers_complete=None, detail=""),
        PullEvent(kind="mutator", ref=long_ref, phase="layer",
                  layers_total=12, layers_complete=3, detail="Downloading"),
        PullEvent(kind="mutator", ref=long_ref, phase="layer",
                  layers_total=12, layers_complete=9, detail="Pull complete"),
        PullEvent(kind="mutator", ref=long_ref, phase="done",
                  layers_total=12, layers_complete=12, detail=""),
    ]
    calls: list[tuple[str, str, bool]] = []   # (ref, kind, on_line was passed)

    def _ensure(ref, kind, on_line=None, on_event=None, **k):
        calls.append((ref, kind, on_line is not None))
        if kind == "mutator" and on_event is not None:
            for ev in scripted:
                on_event(ev)
        return None   # both "images" acquired successfully

    monkeypatch.setattr(ef, "ensure_image", _ensure)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        form = app.query_one(ef.EvolveForm)
        snapshots: list[str] = []
        real_apply = form._apply_pull

        def _spy(event):
            real_apply(event)   # runs on the UI thread (via call_from_thread) --
            snapshots.append(str(form.query_one("#f_pull", Static).render()))

        form._apply_pull = _spy

        form._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()   # the provision-then-launch worker
        await pilot.pause()

        # both images went through the real seam, and never asked for the
        # raw on_line dump this surface replaces
        assert {kind for _ref, kind, _ in calls} == {"mutator", "arena"}
        assert all(not had_on_line for _ref, _kind, had_on_line in calls)
        assert len(snapshots) == 4   # exactly mutator's scripted sequence (arena fired none)

        start_text, _layer1_text, layer2_text, done_text = snapshots
        short = diagnostics._short_digest(long_ref)

        # "start": kind + shortened digest (no 64-hex run) + one-time-pull framing
        assert "mutator" in start_text and short in start_text
        assert "a" * 64 not in start_text          # the raw digest never leaks through
        assert "one-time pull" in start_text

        # "layer": an m/n layers indicator (from the second, 9/12, event)
        assert "9/12" in layer2_text and "layers" in layer2_text

        # "done": a checkmark for that kind
        assert "✓" in done_text and "mutator" in done_text

        # deferred by ruling: no size/GB string anywhere, at any phase
        for text in snapshots:
            assert "gb" not in text.lower()
            assert "size" not in text.lower()

        # #f_err stays untouched -- it's for genuine errors only now
        assert str(app.query_one("#f_err", Static).render()).strip() == ""
        assert isinstance(app.started, _Plan)      # provisioning still launches the run


async def test_pull_error_phase_writes_to_f_err_not_f_pull(monkeypatch):
    """A mid-pull `error`-phase `PullEvent` must surface in #f_err -- the
    genuine-error surface -- not linger in #f_pull. The authoritative,
    one-command-fix message is `ensure_image`'s own return value (mirroring
    the CLI's error mapping), which `_provision_then_launch` writes into
    #f_err right after -- so that's the final state this checks."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)

    def _ensure(ref, kind, on_event=None, **k):
        if kind == "mutator":
            if on_event is not None:
                on_event(PullEvent(kind="mutator", ref=ref, phase="start",
                                   layers_total=None, layers_complete=None, detail=""))
                on_event(PullEvent(kind="mutator", ref=ref, phase="error",
                                   layers_total=2, layers_complete=1, detail="boom"))
            return "[red]couldn't reach the registry[/] — only the first run needs the network"
        return None

    monkeypatch.setattr(ef, "ensure_image", _ensure)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        err_text = str(app.query_one("#f_err", Static).render())
        assert "couldn't reach the registry" in err_text   # the mapped, authoritative message
        assert app.started is None   # provisioning failed -- the run never launched


async def test_pull_error_detail_shows_its_brackets_literally(monkeypatch):
    """An error event's detail is raw build or pull output, which routinely holds
    bracketed text. Parsed as markup, `[internal]` would vanish and `[/nope]`
    would raise; #f_err must show both as written."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)
    detail = "#5 [internal] load metadata for x\n[/nope]"

    def _ensure(ref, kind, on_event=None, **k):
        if kind == "mutator":
            if on_event is not None:
                on_event(PullEvent(kind="mutator", ref=ref, phase="error",
                                   layers_total=None, layers_complete=None, detail=detail))
            return "[red]sandbox setup failed[/]"
        return None

    monkeypatch.setattr(ef, "ensure_image", _ensure)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        form = app.query_one(ef.EvolveForm)
        shown: list[str] = []
        real_apply = form._apply_pull

        def _spy(event):
            real_apply(event)   # on the UI thread (via call_from_thread), like production
            shown.append(str(form.query_one("#f_err", Static).render()))

        form._apply_pull = _spy
        form._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert shown == [detail]


async def test_provisioning_error_renders_its_build_lines_as_the_cli_prints_them(monkeypatch):
    """`ensure_image`'s message is Rich markup with the build's raw lines escaped
    for Rich, and the CLI prints it through Rich. Textual's own parser reads some
    brackets Rich's escape leaves alone (`[ 45%]`, `[Warning]`) as tags, so #f_err
    must read the message with Rich's parser too."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: False)
    raw = ("#5 [internal] load metadata for x\n[/nope]\n"
           "[ 45%] Building C object\n[Warning] low disk space")
    message = ("[red]sandbox setup failed[/] — the mutator image build did not complete. "
               f"Its last lines:\n{escape(raw)}")
    monkeypatch.setattr(ef, "ensure_image",
                        lambda ref, kind, **k: message if kind == "mutator" else None)
    app = _Host(None)
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        shown = str(app.query_one("#f_err", Static).render())

    assert raw in shown
    assert shown == Text.from_markup(message).plain
    assert app.started is None


async def test_operator_switch_uses_cache_second_time(monkeypatch):
    """One probe per operator: switching back to an already-probed operator is
    instant (a cache hit), not another ~1s `docker run`."""
    calls: list[str] = []

    def _probe(backend, **k):
        calls.append(backend)
        return CliInfo(backend, True, f"{backend} x", True), None

    monkeypatch.setattr(ef, "probe_operator", _probe)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()   # mount probes the default (claude)
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        form.query_one("#f_op", Select).value = "codex"      # probes codex
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        form.query_one("#f_op", Select).value = "claude"     # cached -> NO re-probe
        await pilot.pause()
        assert calls == ["claude", "codex"]   # the switch back to claude hit the cache


# ---------------------------------------------------------------------------
# Readiness strip (spec 5.8: "on entering evolve you see what evolve needs").
# `diagnostics.run_checks` is faked at its own module attribute -- the form
# does a fresh `from nethackers import diagnostics; diagnostics.run_checks(
# ...)` lookup at call time (not a name bound into evolve_form's own
# namespace at import time), so patching `diagnostics.run_checks` directly is
# the seam the form actually dereferences.
# ---------------------------------------------------------------------------

async def test_readiness_strip_shows_evolve_checks_and_not_ready_verdict(monkeypatch):
    """On mount the form probes readiness scoped to the `evolve` capability,
    off the UI thread, and -- critically -- NEVER over the network: the
    worker must pass the always-False `manifest_reachable` so opening this
    tab never triggers a GHCR round-trip (spec 5.8)."""
    captured: dict = {}

    def _fake_run_checks(**kwargs):
        captured.update(kwargs)
        return [
            diagnostics.CheckResult(
                id="container_runtime", status="ok", severity="hard",
                detail="a working container runtime is available", fix=None,
                capabilities=("eval", "evolve")),
            diagnostics.CheckResult(
                id="arena_image", status="ok", severity="hard",
                detail="present — arena:dev", fix=None, capabilities=("eval", "evolve")),
            diagnostics.CheckResult(
                id="mutator_image", status="warn", severity="hard",
                detail="not local yet, but pullable — mutator:dev",
                fix="run `nethackers doctor --pull` to fetch it now", capabilities=("evolve",)),
            diagnostics.CheckResult(
                id="operator", status="ok", severity="hard",
                detail="codex: logged in, claude: not logged in", fix=None,
                capabilities=("evolve",),
                items=(diagnostics.CheckItem("codex", "ok", "logged in"),
                       diagnostics.CheckItem("claude", "fail", "not logged in"))),
        ]

    monkeypatch.setattr(diagnostics, "run_checks", _fake_run_checks)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = str(app.query_one("#f_readiness", Static).render())
        assert "✓" in text and "container_runtime" in text   # runtime ready
        assert "✓" in text and "arena_image" in text          # arena ready
        assert "⚠" in text and "mutator_image" in text         # mutator not ready
        # operator renders as a sublist -- each registered agent on its own line
        assert "codex" in text and "claude" in text
        assert "not ready" in text.lower()                    # overall evolve verdict

    # the strip checks ALL registered agents now (operator not narrowed) and
    # never over the network -- spec 5.8:
    assert "operator" not in captured
    assert captured["manifest_reachable"]("anyref") is False
    assert callable(captured["manifest_reachable"])
    # network-off: never says "pullable" by actually reaching the registry
    assert captured["manifest_reachable"]("ghcr.io/dunnolab/nethackers-mutator:dev") is False
    # scoped to ONLY the evolve-tagged (local) checks -- so run_checks itself
    # never even calls the hub/gh/hub_login probes (fix round 1: the strip
    # must not touch the network just because run_checks CAN do more).
    assert set(captured["only"]) == {
        cid for cid, (_sev, caps) in diagnostics.CHECK_SPECS.items() if "evolve" in caps}


async def test_model_select_degrades_when_mutator_image_is_absent(monkeypatch):
    """`probe_operator`'s `installed=False` return is specifically the
    mutator-image-absent case (discovery.py: `if not image_present(image):
    return CliInfo(backend, False, None, None), None`). The curated static
    model list is just as unverifiable in that state, so the picker should
    say why instead of quietly offering "Harness default" (spec 5.8)."""
    monkeypatch.setattr(
        ef, "probe_operator",
        lambda backend, **k: (CliInfo(backend, False, None, None), None))
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        sel = app.query_one("#f_model", Select)
        label = str(sel.query_one("#label", Static).render()).lower()
        assert "pull the sandbox" in label
        # still resolves to "no model pin", same as today's "Harness default"
        assert app.query_one(ef.EvolveForm)._model() is None


# ---------------------------------------------------------------------------
# Objective selection grid (IdentityGrid). The grid's own toggle/token logic
# is unit-tested in test_tui_identity_grid.py; here we verify the FORM wiring
# -- an IdentityGrid.Changed updates the form's `_objective` (the single
# source of truth `_params()` reads) and the chip. The tests above keep poking
# `._objective` directly, which still flows to params unchanged.
# ---------------------------------------------------------------------------

async def test_grid_selection_drives_objective_and_chip():
    app = _Host(None)
    async with app.run_test(size=(120, 50)) as pilot:
        form = app.query_one(ef.EvolveForm)
        grid = form.query_one(IdentityGrid)
        grid.cursor = "role:wiz"
        grid._toggle()                       # select the whole Wizard role
        await pilot.pause()
        assert form._objective == "wiz"      # the form's source of truth updated
        chip = str(form.query_one("#f_obj_sel", Static).render())
        assert "wiz" in chip and "10 build" in chip
        grid.clear_all()
        await pilot.pause()
        assert form._objective is None       # cleared -> back to no objective


async def test_grid_is_a_nav_target_but_scroll_pane_is_not():
    app = NetHackersApp(hub="http://h", creds=None, start="evolve")
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        await pilot.pause()
        targets = app._nav_targets()
        assert app.query_one("#f_obj_grid", IdentityGrid) in targets  # the grid is reachable
        assert app.query_one("#f_objective") not in targets           # its subwindow pane isn't


# ---------------------------------------------------------------------------
# Publish-readiness pre-check at Start (spec 5.6): a hub-logged-in but
# `gh`-unauthed contestant evolves, wins, and the win silently stays local.
# The CLI already warns at evolve Start (cli.py:823-842); this mirrors its
# three branches VERBATIM into the form's own `#f_publish_warn` -- a line
# that must survive past Start (unlike `#f_err`, which provisioning
# overwrites with progress text). The warning is advisory only: it never
# blocks the launch, in any of the three states.
# ---------------------------------------------------------------------------

async def test_publish_warning_shows_gh_unauthed_but_run_still_launches(monkeypatch):
    """Hub-logged-in (a real owner) but `gh auth login` was never run: the
    #1 'wins won't register' onboarding gap (spec 5.6). Must warn AND must
    still launch -- publishing failure is not a reason to block the run."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "gh_state", lambda: ("", "unauthed"))
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        warn = str(app.query_one("#f_publish_warn", Static).render())
        assert "wins won't publish" in warn
        assert "gh auth login" in warn
        assert isinstance(app.started, _Plan)   # non-blocking: the run still launched


async def test_publish_warning_shows_gh_missing_install_hint(monkeypatch):
    """`gh` isn't even on PATH -- a different fix (install it) from merely
    unauthed, so the CLI (and this mirror) distinguish the two messages."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "gh_state", lambda: (None, "missing"))
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        warn = str(app.query_one("#f_publish_warn", Static).render())
        assert "wins won't publish" in warn
        assert "install the github cli" in warn.lower()
        assert isinstance(app.started, _Plan)


async def test_no_publish_warning_when_gh_authed(monkeypatch):
    """Hub-logged-in AND `gh` authed: publishing will actually work, so no
    warning belongs on screen."""
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "gh_state", lambda: ("x", "authed"))
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        warn = str(app.query_one("#f_publish_warn", Static).render()).strip()
        assert warn == ""
        assert isinstance(app.started, _Plan)


async def test_publish_warning_shows_offline_note_when_not_logged_in(monkeypatch):
    """No hub login at all (the form's own `OFFLINE_OWNER` backstop) is a
    different, dimmer note than the gh-specific ones -- and `gh_state` isn't
    even worth calling in that case (mirrors the CLI's `elif` chain, which
    only reaches `gh_state()` when NOT already reporting the offline note)."""
    gh_calls: list[None] = []
    monkeypatch.setattr(ef, "prepare_evolve", lambda *a, **k: _Plan())
    monkeypatch.setattr(ef, "gh_state", lambda: (gh_calls.append(None), ("x", "authed"))[1])
    app = _Host(None)   # no creds -> owner defaults to OFFLINE_OWNER
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        warn = str(app.query_one("#f_publish_warn", Static).render())
        assert "running offline" in warn
        assert "nethackers login" in warn
        assert gh_calls == []                   # gh_state was never even consulted
        assert isinstance(app.started, _Plan)


# ---------------------------------------------------------------------------
# Network toggle (spec 3e): which network the cold-start SELECT reads elites
# from -- "self-reported" (fast, open leaderboard) by default, or "verified"
# (trusted scores on hidden seeds) opt-in. Orthogonal to sandboxing: every
# pulled program still runs in the sealed sandbox either way (INV7).
# ---------------------------------------------------------------------------

async def test_network_toggle_defaults_to_self_reported(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        form = app.query_one(ef.EvolveForm)
        assert form._network_tier() == "self-reported"     # default, before any Start
        # the explanation text sits next to the control and names the safety
        # invariant (sandboxing) that holds regardless of which network is picked
        help_text = str(form.query_one("#f_network_help", Static).render()).lower()
        assert "sealed" in help_text

        form._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert seen["params"].tier == "self-reported"       # flows through to EvolveParams


async def test_selecting_verified_sets_tier(monkeypatch):
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        form = app.query_one(ef.EvolveForm)
        form.query_one("#f_network", Select).value = "verified"
        await pilot.pause()
        assert form._network_tier() == "verified"

        form._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        assert seen["params"].tier == "verified"


# ---------------------------------------------------------------------------
# Detected runtime -> EvolveParams (issue #54). The form already resolved
# docker-vs-podman for its own preflight/presence/provision probes (#50/#52),
# but never put it on the params it hands to `prepare_evolve` -- which is what
# `ContainerOperator(docker=...)` and every arena eval in `run_loop` actually
# shell out to. On a podman-only host that made Start pass its checks and then
# launch a run that exec'd a `docker` binary that isn't there.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("image_built", [True, False])
async def test_start_threads_the_detected_runtime_onto_params(monkeypatch, image_built):
    """Start puts the DETECTED runtime on `params.runtime`, on both launch
    paths -- straight through (image already present) and after provisioning.
    The suite-wide conftest stub pins `container_runtime` to "docker"; this
    test overrides it with a podman-only host."""
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    monkeypatch.setattr(ef, "container_runtime", lambda **k: "podman")
    monkeypatch.setattr(ef, "image_present", lambda *a, **k: image_built)
    monkeypatch.setattr(ef, "ensure_image", lambda *a, **k: None)
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()   # the provision-then-launch worker
        await pilot.pause()

        assert seen["params"].runtime == "podman"
        assert isinstance(app.started, _Plan)


async def test_start_falls_back_to_docker_when_no_runtime_is_detected(monkeypatch):
    """`container_runtime()` returning None can't reach here in practice (the
    sandbox preflight above it already failed), but params.runtime is typed
    `str` -- keep the dataclass default rather than writing None into it."""
    seen: dict = {}

    def _fake_prepare_evolve(params, **_kw):
        seen["params"] = params
        return _Plan()

    monkeypatch.setattr(ef, "prepare_evolve", _fake_prepare_evolve)
    monkeypatch.setattr(ef, "container_runtime", lambda **k: None)
    app = _Host(Credentials("castiel", "tok"))
    async with app.run_test(size=(100, 40)) as pilot:
        app.query_one(ef.EvolveForm)._objective = "wiz-elf-cha-mal"
        app.query_one("#f_start", Button).press()
        await pilot.pause()

        assert seen["params"].runtime == "docker"


def test_publish_warning_names_a_gh_that_did_not_answer(monkeypatch):
    monkeypatch.setattr(ef, "gh_state", lambda: (None, "unknown"))
    warn = ef._publish_warning("castiel")
    assert "didn't answer" in warn
    assert "gh auth login" not in warn


async def test_switching_operator_shows_a_live_check_and_loading_pickers(monkeypatch):
    import threading
    release = threading.Event()

    def _slow_probe(backend, **k):
        if backend == "codex":
            release.wait(5)
        return CliInfo(backend, True, f"{backend} 1.0", True), None

    monkeypatch.setattr(ef, "probe_operator", _slow_probe)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()        # the mount check (claude) is instant
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        form.query_one("#f_op", Select).value = "codex"
        await pilot.pause()
        line = str(form.query_one("#f_op_version", Static).render())
        assert "checking codex in the sandbox" in line   # said at once...
        assert "claude 1.0" not in line                  # ...and the stale line is gone
        assert form.query_one("#f_model", Select).loading is True
        assert form.query_one("#f_effort", Select).loading is True
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "codex 1.0" in str(form.query_one("#f_op_version", Static).render())
        assert form.query_one("#f_model", Select).loading is False
        assert form.query_one("#f_effort", Select).loading is False


async def test_a_check_that_raises_clears_the_spinner(monkeypatch):
    def _probe(backend, **k):
        if backend == "codex":
            raise RuntimeError("docker exploded")
        return CliInfo(backend, True, f"{backend} 1.0", True), None

    monkeypatch.setattr(ef, "probe_operator", _probe)
    app = _Host(None)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        form = app.query_one(ef.EvolveForm)
        form.query_one("#f_op", Select).value = "codex"
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        line = str(form.query_one("#f_op_version", Static).render())
        assert "couldn't check codex" in line
        assert form.query_one("#f_model", Select).loading is False
        assert form.query_one("#f_effort", Select).loading is False
