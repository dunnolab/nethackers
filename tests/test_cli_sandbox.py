# tests/test_cli_sandbox.py
"""The evolve mutator ALWAYS runs sandboxed: ``prepare_evolve`` unconditionally
builds a ``ContainerOperator`` (harness=``--operator``, image=``--mutator-image``)
-- there is no host-execution path. Every evolve run first clears the sandbox
preflight (``cli.sandbox_preflight``: a working container runtime + a resolvable
host login); on failure the CLI prints the preflight's styled message and exits
non-zero *without a traceback*, per the "beautiful CLI, never a traceback" rule
(``cli.py``'s ``main`` top-level guard).

These are pure wiring tests: ``launch.run_loop`` is monkeypatched, and
``cli.sandbox_preflight`` is stubbed per test -- its real logic (docker-runtime
+ login checks) is unit-tested in test_sandbox_preflight.py.
"""
import json

from nethackers import cli
from nethackers.harness import launch
from nethackers.harness.container_operator import ContainerOperator
from nethackers.harness.discovery import CliInfo, Preflight


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    return seed


def _evolve_argv(seed, tmp_path, *extra):
    return ["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
            "--workdir", str(tmp_path / "w"), *extra]


def _stub_preflight_ok(monkeypatch):
    """Both evolve preflights pass: the sandbox (runtime up, login resolvable)
    and the model-availability check (proceed, even for a pinned --model), and
    the sandbox image is already built (no auto-build)."""
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(
        cli, "preflight_model",
        lambda operator, model, **kw: Preflight(
            "proceed", "", CliInfo(operator, True, f"{operator} x", True), None))


# --- selection: evolve always builds a ContainerOperator, passed through to
# run_loop as its `operator=` kwarg (captured via the monkeypatched run_loop,
# exactly like test_cli_evolve.py's wiring tests). ------------------------


def test_evolve_always_builds_container_operator(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflight_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--operator", "codex",
                               "--mutator-image", "my/mutator:tag"))

    assert rc == 0
    op = captured["operator"]
    assert isinstance(op, ContainerOperator)
    assert op.harness == "codex"
    assert op.image == "my/mutator:tag"
    # run_id must be threaded through from launch.py's `rid` so the
    # container name is `mut-${RUN_ID}-${ITER}` (spec §3.3), not a
    # `worktree.parent.name`-derived "work" literal that collides across
    # every run (see container_operator.py's `run_id` param + `run()`).
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    assert op._run_id == run_dir.name


def test_mutator_image_defaults(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflight_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path))

    assert rc == 0
    assert captured["operator"].image == "nethackers/mutator:latest"


def test_model_and_effort_thread_through(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflight_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--model", "gpt-5.6-sol", "--effort", "high"))

    assert rc == 0
    op = captured["operator"]
    assert op.model == "gpt-5.6-sol" and op.effort == "high"


# --- provenance: a run's on-disk run.json records the image the mutator ran
# in -- needed to reproduce/attribute the run (run-isolation-design's run.json
# contract records operator/image/model/effort the same way). -------------


def test_records_mutator_image_in_run_config(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(launch, "run_loop", lambda **kw: [], raising=False)
    _stub_preflight_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--mutator-image", "custom/mutator:tag"))

    assert rc == 0
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["mutator_image"] == "custom/mutator:tag"


# --- preflight failure: the CLI shows the message and exits clean ---------


def test_preflight_failure_exits_nonzero_without_traceback(tmp_path, capsys, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(
        cli, "sandbox_preflight",
        lambda *a, **kw: "[red]sandbox unavailable[/]: no working container runtime "
                         "found — start colima (or Podman), then retry")

    rc = cli.main(_evolve_argv(seed, tmp_path))

    assert rc != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err
    assert "sandbox unavailable" in captured.err.lower()


def test_preflight_message_is_shown_on_stderr(tmp_path, capsys, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(
        cli, "sandbox_preflight",
        lambda *a, **kw: "[red]not logged in[/]: run `codex login` on this host, then retry")

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "codex"))

    assert rc != 0
    assert "codex login" in capsys.readouterr().err


def test_preflight_receives_the_selected_operator(tmp_path, monkeypatch):
    """The preflight must check the login for the harness the run will use."""
    seed = _seed(tmp_path)
    seen = {}

    def _pf(operator, *a, **kw):
        seen["operator"] = operator
        return "[red]not logged in[/]: hint"
    monkeypatch.setattr(cli, "sandbox_preflight", _pf)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "claude"))

    assert rc != 0
    assert seen["operator"] == "claude"


def test_preflight_failure_never_reaches_run_loop(tmp_path, monkeypatch, capsys):
    """The run must fail fast, before run_loop starts. `rc != 0` alone can't
    pin that: if a regression let it fall through, the `_boom` stub below would
    raise AssertionError from inside run_loop, and main()'s generic `except
    Exception -> return 1` guard would swallow THAT into rc=1 too --
    indistinguishable from the correct early return's rc=1. Asserting the
    preflight's own message landed in stderr (proving IT fired) and that the
    generic fallback's "unexpected error" wording did NOT (proving nothing was
    caught-and-swallowed downstream) is what tells the two apart. `_boom` stays
    as a second, independent layer: even if a future wording change weakened
    those assertions, a real regression still crashes loudly here instead of
    silently invoking a real operator/hub call.
    """
    seed = _seed(tmp_path)
    monkeypatch.setattr(
        cli, "sandbox_preflight",
        lambda *a, **kw: "[red]not logged in[/]: run `codex login` on this host, then retry")

    def _boom(**kw):
        raise AssertionError("run_loop must not run when the preflight fails")
    monkeypatch.setattr(launch, "run_loop", _boom, raising=False)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "codex"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "codex login" in captured.err            # the preflight's own message fired
    assert "unexpected error" not in captured.err    # not main()'s generic fallback


# --- auto-provision: a missing sandbox image is built here, not by the user ---


def test_missing_image_triggers_auto_build_then_proceeds(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    built = {}
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: False)   # not built yet
    monkeypatch.setattr(cli, "build_mutator_image",
                        lambda image, **kw: built.update(image=image))  # returns None (ok)
    captured = {}
    monkeypatch.setattr(launch, "run_loop",
                        lambda **kw: captured.update(kw) or [], raising=False)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--mutator-image", "my/mut:tag"))

    assert rc == 0
    assert built.get("image") == "my/mut:tag"     # auto-built the requested image
    assert "operator" in captured                  # and then the run proceeded


def test_auto_build_failure_exits_nonzero_without_starting(tmp_path, capsys, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: False)
    monkeypatch.setattr(cli, "build_mutator_image",
                        lambda *a, **kw: "[red]sandbox setup failed[/] — build did not complete")

    def _boom(**kw):
        raise AssertionError("run_loop must not start when the build failed")
    monkeypatch.setattr(launch, "run_loop", _boom, raising=False)

    rc = cli.main(_evolve_argv(seed, tmp_path))

    assert rc != 0
    assert "setup failed" in capsys.readouterr().err.lower()
