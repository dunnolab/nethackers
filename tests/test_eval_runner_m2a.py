"""Exercises ``nethackers.eval.runner.eval_batch``'s docker-command
construction and Evidence-wrapping for an ``ObjectiveSpec`` batch, with a
fake docker runner and an injected image-digest resolver -- no real Docker
daemon, no NLE, and no shelling out to `docker image inspect`. That shell
lives only in ``_default_image_digest``; the one test here that touches it
covers its NO-shell branch (an already digest-pinned ref, returned verbatim),
so it still never runs a runtime binary.

Mirrors tests/test_eval_runner.py's fake-runner pattern: the fake never
launches a container, it locates the host directory bind-mounted at
``/out`` from the ``cmd`` list ``eval_batch`` builds, and writes a
results.json there itself -- one dict per batch episode, in batch order.
"""

import dataclasses
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from nethackers import _image_pins
from nethackers.arena.seeds import trajectory_spec
from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval import runner as eval_runner
from nethackers.eval.runner import _default_image_digest, eval_batch

_SPEC = ObjectiveSpec(
    name="m2a-smoke-2",
    kind="batch",
    batch=((0, "val-dwa-law-fem"), (1, "wiz-elf-cha-mal")),
    max_steps=200,
    no_progress_timeout=200,
    action_timeout_seconds=5.0,
    aggregation="mean",
)

# Per-episode dicts a real container would write to /out/results.json,
# matching _SPEC.batch's order and characters. Includes `character` and
# `milestone` (M2a fields) so the test pins that eval_batch threads both
# through into the wrapped Evidence.
_RESULTS = [
    {
        "trajectory_id": 0,
        "status": "completed",
        "progress": 0.5,
        "ascended": False,
        "steps": 3,
        "turns": 2,
        "max_depth": 2,
        "end_status": "died",
        "error": None,
        "wall_seconds": 0.1,
        "character": "val-dwa-law-fem",
        "milestone": "mines-entrance",
    },
    {
        "trajectory_id": 1,
        "status": "completed",
        "progress": 1.0,
        "ascended": True,
        "steps": 9,
        "turns": 8,
        "max_depth": 30,
        "end_status": "ascended",
        "error": None,
        "wall_seconds": 0.4,
        "character": "wiz-elf-cha-mal",
        "milestone": "ascended",
    },
]


def _make_fake_docker_run(calls, inputs=None):
    """Build a fake ``runner``: records the ``cmd`` it was called with (and,
    when ``inputs`` is given, the piped ``input=`` payload too -- the
    pre-derived specs eval_batch now feeds the container over stdin instead
    of a secret on argv/env), then -- instead of launching a container --
    writes a results.json directly into the host directory the real docker
    command would have bind-mounted at /out (found via the "-v <host>:/out"
    argument)."""

    def fake(cmd, check=True, input=None):
        calls.append(cmd)
        assert check is True
        if inputs is not None:
            inputs.append(input)
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps(_RESULTS))

    return fake


def test_eval_batch_wraps_container_results_into_evidence(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls = []

    ev = eval_batch(
        sol,
        _SPEC,
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
    )

    # Per-episode characters carry through, in batch order.
    assert [r.character for r in ev.results] == ["val-dwa-law-fem", "wiz-elf-cha-mal"]
    assert [r.milestone for r in ev.results] == ["mines-entrance", "ascended"]

    # evaluator_image is the *resolved digest*, not the tag passed in.
    assert ev.evaluator_image == "img@sha256:deadbeef"

    # Aggregation.
    assert ev.episodes == 2
    assert ev.mean_progress == 0.75  # mean(0.5, 1.0)
    assert ev.ascensions == 1
    assert ev.tier == "self-reported"
    assert ev.solution_digest.startswith("sha256:")

    # Resolution B: objective is a synthesized, multi-character descriptor --
    # character=None (no single build represents a batch), seed_set carries
    # the spec's name so the descriptor is still traceable to its batch, and
    # the remaining knobs are straight passthroughs from spec.
    assert ev.objective.character is None
    assert ev.objective.seed_set == _SPEC.name
    assert ev.objective.max_steps == _SPEC.max_steps
    assert ev.objective.no_progress_timeout == _SPEC.no_progress_timeout
    assert ev.objective.action_timeout_seconds == _SPEC.action_timeout_seconds

    # Command-building: sealed box (offline_flags, threat 4), solution
    # mounted read-only, and no per-episode seed data on argv at all -- the
    # concrete specs are derived host-side and piped in over stdin instead
    # (Task 7, threat 3 a,b -- see test_eval_batch_never_puts_the_secret_
    # in_the_container below for that wiring).
    cmd = calls[0]
    # No --platform here: "img:dev" is an override, not the pin. See
    # test_platform_flag_is_scoped_to_the_pinned_arena_image below.
    assert cmd[:2] == ["docker", "run"]
    assert "-i" in cmd  # keeps stdin open so the container can read the specs
    assert f"{sol}:/sol:ro" in cmd
    assert "--batch" not in cmd
    assert "--character" not in cmd
    assert "--seeds" not in cmd
    # Sealed box (spec sec3b, threat 4): --network none now lives inside
    # offline_flags() (after --name/--label), alongside the read-only
    # rootfs, dropped capabilities, no-new-privileges, and resource caps --
    # plus an in-image wall-clock timeout as an outer DoS bound.
    assert cmd[cmd.index("--network") + 1] == "none"
    for need in [
        "--read-only", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges", "--pids-limit", "--memory", "--cpu-quota",
        "--user", "timeout",
    ]:
        assert need in cmd, need


def test_arena_run_is_wrapped_by_an_entrypoint_override_timeout(tmp_path):
    """CRITICAL fix-round-1 #1: the wall-clock timeout must WRAP the arena
    entrypoint via ``--entrypoint timeout``, not sit as a trailing CMD arg
    after the image -- the latter shape gets swallowed as bogus argv to the
    image's baked ``python -m nethackers.arena.run`` entrypoint, which
    argparse rejects ("unrecognized arguments: timeout 3600"), failing
    EVERY real ``eval_batch`` call (self-report eval/submit, evolve scoring,
    the verified-tier worker). Only a real docker daemon caught that
    regression -- every other test in this file stayed green -- so this
    structural check on the fake-captured argv is the enforceable
    regression gate until Task 8 rebuilds+re-pins the arena image for a
    real docker-gated smoke."""
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls: list = []

    eval_batch(sol, _SPEC, "img:dev", now="t",
               runner=_make_fake_docker_run(calls),
               image_digest_resolver=lambda img: "img@sha256:deadbeef")

    cmd = calls[0]
    # --entrypoint OVERRIDES the image's baked entrypoint with bare `timeout`...
    assert cmd[cmd.index("--entrypoint") + 1] == "timeout"
    # ...so the post-image command must re-state the FULL invocation as
    # timeout's own argv (`timeout <seconds> python -m nethackers.arena.run
    # --solution /sol ...`) -- NOT `--solution /sol ...` right after the
    # image, which is exactly the regression shape this test pins against.
    image_idx = cmd.index("img:dev")
    assert cmd[image_idx + 1] == str(eval_runner.WALL_TIMEOUT_S)
    assert cmd[image_idx + 2 : image_idx + 5] == ["python", "-m", "nethackers.arena.run"]
    assert cmd[image_idx + 5] == "--solution"


@pytest.mark.parametrize(
    "image, expect_platform",
    [
        (_image_pins.ARENA_IMAGE, True),   # the pin: amd64 manifest, flag is cosmetic
        ("nethackers/arena:dev", False),   # a local build / --image override
        ("arena:some-worktree-slug", False),   # docs/local-stack.md's per-worktree tag
    ],
)
def test_platform_flag_is_scoped_to_the_pinned_arena_image(tmp_path, image, expect_platform):
    """``--platform linux/amd64`` is cosmetic on the pin (an amd64 MANIFEST
    digest already decides the architecture) but FATAL on anything else: docker
    run --platform linux/amd64 against a locally built arm64-only image errors
    with "pull access denied" rather than running it. Passing it unconditionally
    therefore killed both paths spec D6 keeps open -- ``--image`` /
    ``NETHACKERS_ARENA_IMAGE`` for arena development, and the per-worktree
    ``arena:<slug>`` of docs/local-stack.md -- on every arm64 host."""
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls: list = []

    eval_batch(sol, _SPEC, image, now="t", runner=_make_fake_docker_run(calls),
               image_digest_resolver=lambda img: "img@sha256:deadbeef")

    cmd = calls[0]
    assert ("--platform" in cmd) is expect_platform
    if expect_platform:
        assert cmd[:4] == ["docker", "run", "--platform", "linux/amd64"]
        assert cmd[4] == "--rm"
    else:
        assert cmd[:2] == ["docker", "run"]
        assert cmd[2] == "--rm"
    # Sealed regardless of the platform flag's presence -- see
    # test_eval_batch_wraps_container_results_into_evidence for the full
    # sealed-flag membership check.
    assert cmd[cmd.index("--network") + 1] == "none"


def test_already_pinned_refs_skip_the_runtime_round_trip():
    """A digest-pinned ref is its own answer, so ``_default_image_digest``
    returns it verbatim instead of asking the runtime. That string gates hub
    admission (spec D5), and ``image inspect`` would hand back
    ``RepoDigests[0]`` -- a LIST whose order is a runtime implementation
    detail, so podman (issue #50) or a mirrored repo entry could turn a
    correctly pinned run into an unclassified one. ``runtime`` is set to a
    binary that does not exist: if this ever shelled out, the test would
    raise rather than silently pass."""
    assert _default_image_digest(_image_pins.ARENA_IMAGE, runtime="no-such-runtime-binary") == (
        _image_pins.ARENA_IMAGE
    )


def test_eval_output_mount_uses_home_backed_managed_tmp(tmp_path, monkeypatch):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []

    eval_batch(
        sol,
        _SPEC,
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
    )

    out_mount = next(v for v in calls[0] if v.endswith(":/out"))
    host_out = Path(out_mount.removesuffix(":/out"))
    assert host_out.parent == tmp_path / ".nethackers" / "tmp"
    assert host_out.name.startswith("arena-")
    assert not host_out.exists()  # TemporaryDirectory still cleans each run.


def test_eval_temp_dir_is_world_writable_for_the_nobody_arena(tmp_path, monkeypatch):
    # The arena container writes results.json into /out as uid 65534 (nobody);
    # tempfile creates the dir 0700 owned by whoever launched the eval, which
    # that uid can't write on native Linux (Docker Desktop's uid remap hides it
    # on macOS). The ephemeral output dir must be world-writable, or the arena
    # fails PermissionError and evolve crashes.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with eval_runner._eval_temp_dir() as td:
        assert Path(td).stat().st_mode & 0o777 == 0o777, oct(Path(td).stat().st_mode)


@pytest.mark.parametrize("unusable", ["root_blocked", "root_read_only"])
def test_eval_output_mount_falls_back_to_system_temp(tmp_path, monkeypatch, unusable):
    # Both ways ~/.nethackers/tmp can be unusable: its root can't be created,
    # or it exists but refuses a child. Either way the eval must still run,
    # from the system temp dir, without writing anything beside the solution.
    if unusable == "root_read_only" and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    parent = tmp_path / "solutions"
    sol = parent / "sol"
    sol.mkdir(parents=True)
    (sol / "bot.py").write_text("x")
    home = tmp_path / "home"
    home.mkdir()
    if unusable == "root_blocked":
        (home / ".nethackers").write_text("a file where the directory should be")
    else:
        (home / ".nethackers" / "tmp").mkdir(parents=True)
        (home / ".nethackers" / "tmp").chmod(0o555)
    monkeypatch.setattr(Path, "home", lambda: home)
    system_tmp = tmp_path / "system-tmp"
    system_tmp.mkdir()
    monkeypatch.setattr(eval_runner.tempfile, "tempdir", str(system_tmp))
    calls = []

    eval_batch(
        sol,
        _SPEC,
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
    )

    out_mount = next(v for v in calls[0] if v.endswith(":/out"))
    assert Path(out_mount.removesuffix(":/out")).parent == system_tmp
    assert [p.name for p in parent.iterdir()] == ["sol"]


def test_eval_batch_absolutizes_relative_solution_mount(tmp_path, monkeypatch):
    """Docker rejects a relative bind-mount source (it reads ``roots/autoascend``
    as an invalid named volume). eval_batch must absolutize the solution path
    before the ``-v`` mount, so a caller may pass a repo-relative tree -- e.g.
    the AutoAscend baseline compute passes ``roots/autoascend``. This docker
    path is otherwise only exercised against a real daemon, so the bug slipped
    past the fake-runner tests, which all pass tmp_path-absolute trees."""
    sol = tmp_path / "roots" / "autoascend"
    sol.mkdir(parents=True)
    (sol / "bot.py").write_text("x")
    monkeypatch.chdir(tmp_path)
    calls: list = []

    eval_batch(
        Path("roots/autoascend"),  # relative, exactly as baseline_compute passes it
        _SPEC,
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
    )

    src = next(v.removesuffix(":/sol:ro") for v in calls[0] if v.endswith(":/sol:ro"))
    assert Path(src).is_absolute(), f"mount source must be absolute, got {src!r}"
    assert Path(src).resolve() == sol.resolve()


def test_eval_batch_passes_max_parallel_evals(tmp_path):
    # Mirrors test_eval_batch_wraps_container_results_into_evidence's setup --
    # this only pins the new --max-parallel-evals docker argv, reusing the
    # same fake-runner helper (records cmd, writes results.json to /out).
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls = []

    eval_batch(
        sol,
        _batch_of(15),
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
        max_parallel_evals=5,
    )

    cmd = calls[0]
    assert "--max-parallel-evals" in cmd
    assert cmd[cmd.index("--max-parallel-evals") + 1] == "5"


def _batch_of(n):
    return dataclasses.replace(_SPEC, batch=tuple((i, "val-dwa-law-fem") for i in range(n)))


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


GiB = 1 << 30


def _run_sized(tmp_path, monkeypatch, *, batch, capacity, requested=None):
    """eval_batch on a machine that reports ``capacity`` = (cpus, memory
    bytes), or None for a runtime that couldn't be asked; returns the argv."""
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    asked = []
    monkeypatch.setattr(eval_runner, "runtime_capacity",
                        lambda runtime: asked.append(runtime) or capacity)
    calls = []
    eval_batch(sol, _batch_of(batch), "img:dev", now="t", runner=_make_fake_docker_run(calls),
               image_digest_resolver=lambda img: "img@sha256:deadbeef",
               max_parallel_evals=requested)
    assert asked == ["docker"]   # the runtime that will run the box is the one asked
    return calls[0]


def _assert_box(cmd, *, episodes, memory):
    assert _flag(cmd, "--max-parallel-evals") == str(episodes)
    assert "--cpus" not in cmd   # refused above the daemon's CPU count -- see sandbox_flags
    assert int(_flag(cmd, "--cpu-quota")) == episodes * int(_flag(cmd, "--cpu-period"))
    assert _flag(cmd, "--memory") == _flag(cmd, "--memory-swap") == f"{memory >> 20}m"
    assert _flag(cmd, "--pids-limit") == str(episodes * eval_runner._PIDS_PER_EPISODE)


@pytest.mark.parametrize(
    "capacity, batch, episodes, memory",
    [
        ((16, 32 * GiB), 15, 15, 24 * GiB),   # a big Mac: the whole identity batch at once
        ((16, 8 * GiB), 15, 6, 6 * GiB),      # many cores, little memory: memory decides
        ((4, 32 * GiB), 15, 4, 24 * GiB),     # the 4-CPU eval node: cores decide
        ((10, 16 * GiB), 2, 2, 12 * GiB),     # a batch smaller than the machine
    ],
)
def test_unset_parallelism_is_sized_from_the_machine(tmp_path, monkeypatch, capsys,
                                                     capacity, batch, episodes, memory):
    """Unset --max-parallel-evals means: one episode per CPU the runtime has,
    as many as 3/4 of its memory holds at 1 GiB each, never more than the
    batch -- and the box's caps follow from that count. v0.34.0 instead fixed
    the box at --cpus 2 / --memory 4g on every machine: 8 episodes shared 2
    cores (3.7x slower, measured), and 15 OOM-killed bots, which the arena
    scores as bot_error at progress 0 while the eval exits 0."""
    cmd = _run_sized(tmp_path, monkeypatch, batch=batch, capacity=capacity)
    _assert_box(cmd, episodes=episodes, memory=memory)
    assert capsys.readouterr().err == ""   # the machine's own size never warns


def test_an_explicit_request_wins_over_the_machine(tmp_path, monkeypatch, capsys):
    # 8 on a 4-CPU box oversubscribes on purpose; the quota form is what lets
    # docker accept 8 cores' worth there at all.
    cmd = _run_sized(tmp_path, monkeypatch, batch=15, capacity=(4, 32 * GiB), requested=8)
    _assert_box(cmd, episodes=8, memory=24 * GiB)
    assert capsys.readouterr().err == ""


def test_a_request_the_memory_cannot_hold_warns(tmp_path, monkeypatch, capsys):
    cmd = _run_sized(tmp_path, monkeypatch, batch=15, capacity=(16, 8 * GiB), requested=15)
    _assert_box(cmd, episodes=15, memory=6 * GiB)
    err = capsys.readouterr().err
    assert "15 episodes" in err and "scores 0" in err and "--max-parallel-evals" in err


@pytest.mark.parametrize("requested, episodes", [(None, 8), (5, 5)])
def test_an_unreadable_machine_falls_back_to_the_fixed_default(tmp_path, monkeypatch,
                                                              requested, episodes):
    cmd = _run_sized(tmp_path, monkeypatch, batch=15, capacity=None, requested=requested)
    _assert_box(cmd, episodes=episodes, memory=episodes * GiB)


@pytest.mark.parametrize(
    "batch, parallel, waves",
    [
        (90, 8, 12),    # a 6-identity cold-start union: each worker runs 12 in turn
        (15, 8, 2),
        (15, 15, 1),
        (0, 8, 1),      # an empty batch still gets a finite bound (`timeout 0` means none)
    ],
)
def test_the_wall_clock_bound_is_per_wave(tmp_path, batch, parallel, waves):
    """The outer `timeout` wraps the WHOLE batch and is all-or-nothing --
    results.json is written only after the last episode -- so a fixed bound
    kills every batch that is long enough, losing all of it. Each worker runs
    its share one episode after another, so the bound is per wave."""
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls = []

    eval_batch(sol, _batch_of(batch), "img:dev", now="t", runner=_make_fake_docker_run(calls),
               image_digest_resolver=lambda img: "img@sha256:deadbeef",
               max_parallel_evals=parallel)

    cmd = calls[0]
    assert cmd[cmd.index("img:dev") + 1] == str(waves * eval_runner.WALL_TIMEOUT_S)


def test_eval_batch_streams_per_episode_when_on_episode_given(tmp_path):
    # The opt-in streaming path (Popen) forwards each parsed per-episode stderr
    # line to on_episode; results still come from results.json (display-only).
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    events: list[dict] = []

    lines = [
        "arena · running 2 episode(s)…\n",
        "/sol/autoascend/agent.py:371: RuntimeWarning: overflow ...\n",
        "arena · episode 1/2 (val-dwa-law-fem): progress=0.5 completed turns=10 depth=2\n",
        "arena · episode 2/2 (wiz-elf-cha-mal): progress=1.0 ascended turns=20 depth=30\n",
    ]

    class _FakeStdin:
        """A ``.write()``/``.close()`` sink that keeps its content readable
        after ``close()`` -- unlike a real ``io.StringIO``, whose buffer is
        gone once closed -- so the test can inspect what got written."""

        def __init__(self) -> None:
            self.value = ""

        def write(self, data: str) -> None:
            self.value += data

        def close(self) -> None:
            pass

    class _FakeProc:
        def __init__(self, out_dir: str) -> None:
            Path(out_dir, "results.json").write_text(json.dumps(_RESULTS))
            self.stderr = iter(lines)
            self.stdin = _FakeStdin()  # the streaming path writes specs here too

        def wait(self) -> int:
            return 0

    procs: list = []

    def fake_popen(cmd, *, stdin, stdout, stderr, text, bufsize):
        out_dir = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        proc = _FakeProc(out_dir)
        procs.append(proc)
        return proc

    ev = eval_batch(
        sol, _SPEC, "img:dev", now="2026-08-09T00:00:00Z",
        on_episode=events.append, popen=fake_popen,
        image_digest_resolver=lambda img: "img@sha256:x",
    )

    # Only the two "episode k/N" lines are forwarded; banner + warning ignored.
    assert [e["index"] for e in events] == [1, 2]
    assert events[0]["seed"] == _SPEC.batch[0][0]  # seed comes from spec order
    assert events[0]["character"] == "val-dwa-law-fem"
    assert events[1]["progress"] == 1.0 and events[1]["depth"] == 30
    # Authoritative aggregate still comes from results.json.
    assert ev.episodes == 2 and ev.ascensions == 1
    # The streaming (Popen) path feeds the container the same pre-derived
    # specs the non-streaming (runner) path does -- only the invocation
    # mechanism differs, not what the container receives.
    streamed_payload = json.loads(procs[0].stdin.value)
    assert streamed_payload[0]["character"] == "val-dwa-law-fem"
    assert "core_seed" in streamed_payload[0]["spec"]


def test_eval_batch_attaches_docker_stderr_on_nonzero_exit(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    lines = [
        "arena · running 2 episode(s)…\n",
        "Unable to find image 'img:dev' locally\n",
        "docker: Error response from daemon: pull access denied for img.\n",
    ]

    class _FailProc:
        def __init__(self) -> None:
            self.stderr = iter(lines)
            self.stdin = io.StringIO()
        def wait(self) -> int:
            return 125

    def fake_popen(cmd, *, stdin, stdout, stderr, text, bufsize):
        return _FailProc()

    with pytest.raises(subprocess.CalledProcessError) as ei:
        eval_batch(sol, _SPEC, "img:dev", now="2026-08-09T00:00:00Z",
                   on_episode=lambda e: None, popen=fake_popen,
                   image_digest_resolver=lambda img: "img@sha256:x")
    assert ei.value.returncode == 125
    assert "pull access denied" in (ei.value.stderr or "")


def test_eval_batch_never_puts_the_secret_in_the_container(tmp_path):
    """threat 3 (a),(b) / INV3: the secret must reach neither the container's
    argv nor its environment -- eval_batch now derives the concrete
    per-trajectory seeds HOST-side and pipes only those, pre-derived, in over
    stdin. This is the trusted path worker/verify.py uses with the (hidden)
    verified-tier secret, so a leak here is the whole point of Task 7."""
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls: list = []
    inputs: list = []

    eval_batch(
        sol, _SPEC, "img:dev", now="2026-09-03T00:00:00Z",
        runner=_make_fake_docker_run(calls, inputs),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
        secret="TOPSECRET",
    )

    cmd = calls[0]
    joined = " ".join(cmd)
    assert "TOPSECRET" not in joined
    assert "NETHACK_ARENA_SECRET" not in joined
    assert "--secret" not in cmd
    assert "--batch" not in cmd
    assert "--evaluation-id" not in cmd

    # The concrete seeds arrive only on stdin, as pre-derived specs -- and are
    # the SAME seeds a container-side derivation would have produced (INV3:
    # same secret + seed => same games -- moving the derivation host-side
    # must not change scoring).
    payload = json.loads(inputs[0])
    assert [entry["character"] for entry in payload] == list(_SPEC.characters())
    for (seed, _char), entry in zip(_SPEC.batch, payload, strict=True):
        assert entry["spec"] == trajectory_spec("TOPSECRET", "local", seed).to_dict()


def test_eval_batch_derives_seeds_with_the_default_public_secret(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls: list = []
    inputs: list = []

    eval_batch(sol, _SPEC, "img:dev", now="2026-09-03T00:00:00Z",
               runner=_make_fake_docker_run(calls, inputs),
               image_digest_resolver=lambda img: "img@sha256:deadbeef")

    assert "NETHACK_ARENA_SECRET" not in " ".join(calls[0])
    payload = json.loads(inputs[0])
    assert payload[0]["spec"] == trajectory_spec("public", "local", _SPEC.batch[0][0]).to_dict()


def test_eval_batch_makes_a_private_solution_dir_traversable(tmp_path):
    # The arena runs the bot as uid 65534 and chdirs into /sol; a caller that
    # builds the tree in a private 0700 dir (verify_program clones the candidate
    # into a 0700 TemporaryDirectory) leaves it unreadable by that uid, so every
    # episode PermissionErrors at turn 0. eval_batch must add o+rx to the mount
    # root so the non-root arena can enter it.
    # A restrictive umask (a domain-joined verifier runs 077) makes the clone's
    # own files 0600 -- unreadable by 65534 -- so the whole tree, not just the
    # mount root, must be opened up: dirs o+rx, files o+r, recursively.
    sol = tmp_path / "sol"
    sol.mkdir(mode=0o700)
    (sol / "bot.py").touch()
    (sol / "bot.py").chmod(0o600)              # top-level file, owner-only
    sub = sol / "autoascend"
    sub.mkdir(mode=0o700)                       # nested dir
    (sub / "agent.py").touch()
    (sub / "agent.py").chmod(0o600)            # nested file, owner-only
    assert sol.stat().st_mode & 0o755 == 0o700

    eval_batch(sol, _SPEC, "img:dev", now="2026-08-09T00:00:00Z",
               runner=_make_fake_docker_run([]),
               image_digest_resolver=lambda img: "img@sha256:deadbeef")

    def mode(p):
        return p.stat().st_mode
    assert mode(sol) & 0o055 == 0o055, ("root", oct(mode(sol)))
    assert mode(sol / "bot.py") & 0o044 == 0o044, ("file", oct(mode(sol / "bot.py")))
    assert mode(sub) & 0o055 == 0o055, ("subdir", oct(mode(sub)))
    assert mode(sub / "agent.py") & 0o044 == 0o044, ("nested", oct(mode(sub / "agent.py")))
