# tests/test_cli_sandbox.py
"""``evolve --sandbox`` wiring: the flag makes ``launch.prepare_evolve``'s
operator-construction branch build a ``ContainerOperator`` (harness=
``--operator``, image=``--mutator-image``) instead of the host
``ClaudeOperator``/``CodexOperator``, gated by two preflights -- a working
container runtime, and the selected harness's host login -- that must both
exit cleanly (no traceback) on failure, per the "beautiful CLI, never a
traceback" rule (``cli.py``'s ``main`` top-level guard).

``launch.run_loop`` is monkeypatched in every test (as in test_cli_evolve.py)
so these are pure wiring tests: no real hub, docker, or coding-agent CLI is
ever invoked. The two preflight checks (``cli._docker_available`` and
``cli.auth_docker_args``) are monkeypatched independently so each test
exercises exactly one thing.
"""
import json

from nethackers import cli
from nethackers.harness import launch
from nethackers.harness.auth_inject import AuthUnavailable
from nethackers.harness.container_operator import ContainerOperator


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


def _stub_preflights_ok(monkeypatch):
    """Both preflights pass: a working runtime, and a resolvable login."""
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    monkeypatch.setattr(cli, "auth_docker_args", lambda *a, **kw: [])


# --- selection: --sandbox builds a ContainerOperator, passed through to
# run_loop as its `operator=` kwarg (captured via the monkeypatched run_loop,
# exactly like test_cli_evolve.py's wiring tests). ------------------------


def test_sandbox_selects_container_operator_with_harness_and_image(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflights_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--operator", "codex", "--sandbox",
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


def test_sandbox_defaults_mutator_image(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflights_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--sandbox"))

    assert rc == 0
    assert captured["operator"].image == "nethackers/mutator:latest"


def test_sandbox_passes_model_and_effort_through(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)
    _stub_preflights_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--sandbox",
                               "--model", "gpt-5.6-sol", "--effort", "high"))

    assert rc == 0
    op = captured["operator"]
    assert op.model == "gpt-5.6-sol" and op.effort == "high"


# --- provenance: a run's on-disk run.json must show whether it was
# sandboxed, and in which image -- needed to reproduce/attribute the run
# (docs/superpowers/specs/2026-08-12-run-isolation-design.md's run.json
# contract already records operator/image/model/effort the same way). -----


def test_sandbox_records_sandbox_and_mutator_image_in_run_config(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(launch, "run_loop", lambda **kw: [], raising=False)
    _stub_preflights_ok(monkeypatch)

    rc = cli._run(_evolve_argv(seed, tmp_path, "--sandbox",
                               "--mutator-image", "custom/mutator:tag"))

    assert rc == 0
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["sandbox"] is True
    assert cfg["mutator_image"] == "custom/mutator:tag"


def test_non_sandbox_records_sandbox_false_and_default_mutator_image(tmp_path, monkeypatch):
    """run.json carries both keys unconditionally, sandboxed or not -- a
    run's provenance shouldn't change shape depending on the mode it ran
    in (same convention as model/effort, which are recorded even when
    unset)."""
    seed = _seed(tmp_path)
    monkeypatch.setattr(launch, "run_loop", lambda **kw: [], raising=False)

    rc = cli._run(_evolve_argv(seed, tmp_path))

    assert rc == 0
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["sandbox"] is False
    assert cfg["mutator_image"] == "nethackers/mutator:latest"


def test_without_sandbox_keeps_using_the_host_operator(tmp_path, monkeypatch):
    """No regression: omitting --sandbox must NOT go anywhere near the
    preflights or ContainerOperator -- the host branch is unaffected."""
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [],
                        raising=False)

    def _boom(*a, **kw):
        raise AssertionError("docker preflight must not run without --sandbox")
    monkeypatch.setattr(cli, "_docker_available", _boom)

    rc = cli._run(_evolve_argv(seed, tmp_path))

    assert rc == 0
    assert not isinstance(captured["operator"], ContainerOperator)


# --- docker preflight: missing/broken runtime exits clean, no traceback --


def test_sandbox_missing_docker_exits_nonzero_without_traceback(tmp_path, capsys, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "_docker_available", lambda: False)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--sandbox"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err


def test_docker_available_false_when_which_finds_nothing(tmp_path, monkeypatch, capsys):
    """Exercise the real _docker_available (not the stub) with an injected
    `which` miss -- the CLI checks for a working runtime, not just belief in
    one existing."""
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--sandbox"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err


def test_docker_available_false_when_docker_info_fails(tmp_path, monkeypatch, capsys):
    """`docker` is on PATH but the daemon/VM is unreachable (`docker info`
    exits non-zero) -- a stopped Colima VM looks exactly like this; the
    preflight must catch it, not just the PATH lookup."""
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker")

    class _FailedInfo:
        returncode = 1
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: _FailedInfo())

    rc = cli.main(_evolve_argv(seed, tmp_path, "--sandbox"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err


def test_sandbox_docker_hint_mentions_a_bring_up_command(tmp_path, monkeypatch, capsys):
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "_docker_available", lambda: False)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--sandbox"))

    assert rc != 0
    err_text = capsys.readouterr().err.lower()
    assert "colima" in err_text or "podman" in err_text or "docker" in err_text


# --- auth preflight: an unresolvable login exits clean, with the hint ----


def test_sandbox_auth_unavailable_exits_nonzero_with_hint(tmp_path, capsys, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "_docker_available", lambda: True)

    def _raise(*a, **kw):
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
    monkeypatch.setattr(cli, "auth_docker_args", _raise)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "codex", "--sandbox"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err
    assert "codex login" in captured.err


def test_sandbox_auth_preflight_uses_the_selected_operator_as_harness(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    seen = {}

    def _raise(harness, **kw):
        seen["harness"] = harness
        raise AuthUnavailable(harness, "hint")
    monkeypatch.setattr(cli, "auth_docker_args", _raise)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "claude", "--sandbox"))

    assert rc != 0
    assert seen["harness"] == "claude"


def test_sandbox_auth_preflight_never_reaches_run_loop(tmp_path, monkeypatch, capsys):
    """The ruling requires this fail fast, before run_loop starts. `rc != 0`
    alone can't pin that: if a regression let this fall through, the `_boom`
    stub below would raise AssertionError from inside run_loop, and main()'s
    generic `except Exception -> return 1` guard would swallow THAT into
    rc=1 too -- indistinguishable from the correct early return's rc=1.
    Asserting the preflight's own hint landed in stderr (proving IT is what
    fired) and that the generic fallback's "unexpected error" wording did
    NOT (proving nothing was caught-and-swallowed downstream) is what
    actually tells the two apart. `_boom` stays as a second, independent
    layer: even if a future stderr-wording change weakened those two
    assertions, a real regression still crashes loudly here instead of
    silently invoking a real operator/hub call.
    """
    seed = _seed(tmp_path)
    monkeypatch.setattr(cli, "_docker_available", lambda: True)

    def _raise(*a, **kw):
        raise AuthUnavailable("codex", "run `codex login` on this host, then retry")
    monkeypatch.setattr(cli, "auth_docker_args", _raise)

    def _boom(**kw):
        raise AssertionError("run_loop must not run when auth is unavailable")
    monkeypatch.setattr(launch, "run_loop", _boom, raising=False)

    rc = cli.main(_evolve_argv(seed, tmp_path, "--operator", "codex", "--sandbox"))

    assert rc != 0
    captured = capsys.readouterr()
    assert "codex login" in captured.err            # the preflight's own message fired
    assert "unexpected error" not in captured.err    # not main()'s generic fallback
