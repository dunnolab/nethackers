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

import json
import os
import subprocess
from pathlib import Path

import pytest

from nethackers import _image_pins
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


def _make_fake_docker_run(calls):
    """Build a fake ``runner``: records the ``cmd`` it was called with, then
    -- instead of launching a container -- writes a results.json directly
    into the host directory the real docker command would have bind-mounted
    at /out (found via the "-v <host>:/out" argument)."""

    def fake(cmd, check):
        calls.append(cmd)
        assert check is True
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

    # Command-building: --network none, solution mounted read-only, and the
    # batch passed as [[seed, character], ...] JSON (not --character/--seeds).
    cmd = calls[0]
    # No --platform here: "img:dev" is an override, not the pin. See
    # test_platform_flag_is_scoped_to_the_pinned_arena_image below.
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{sol}:/sol:ro" in cmd
    expected_batch_arg = json.dumps([[seed, char] for seed, char in _SPEC.batch])
    assert cmd[cmd.index("--batch") + 1] == expected_batch_arg
    assert "--character" not in cmd
    assert "--seeds" not in cmd


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
        assert cmd[:7] == [
            "docker", "run", "--platform", "linux/amd64", "--rm", "--network", "none",
        ]
    else:
        assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]


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
        _SPEC,
        "img:dev",
        now="2026-08-09T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
        max_parallel_evals=5,
    )

    cmd = calls[0]
    assert "--max-parallel-evals" in cmd
    assert cmd[cmd.index("--max-parallel-evals") + 1] == "5"


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

    class _FakeProc:
        def __init__(self, out_dir: str) -> None:
            Path(out_dir, "results.json").write_text(json.dumps(_RESULTS))
            self.stderr = iter(lines)

        def wait(self) -> int:
            return 0

    def fake_popen(cmd, *, stdout, stderr, text, bufsize):
        out_dir = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        return _FakeProc(out_dir)

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
        def wait(self) -> int:
            return 125

    def fake_popen(cmd, *, stdout, stderr, text, bufsize):
        return _FailProc()

    with pytest.raises(subprocess.CalledProcessError) as ei:
        eval_batch(sol, _SPEC, "img:dev", now="2026-08-09T00:00:00Z",
                   on_episode=lambda e: None, popen=fake_popen,
                   image_digest_resolver=lambda img: "img@sha256:x")
    assert ei.value.returncode == 125
    assert "pull access denied" in (ei.value.stderr or "")


def test_eval_batch_passes_secret_via_env_not_argv(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls = []
    eval_batch(
        sol, _SPEC, "img:dev", now="2026-09-03T00:00:00Z",
        runner=_make_fake_docker_run(calls),
        image_digest_resolver=lambda img: "img@sha256:deadbeef",
        secret="s3cr3t",
    )
    cmd = calls[0]
    assert "-e" in cmd and "NETHACK_ARENA_SECRET=s3cr3t" in cmd
    assert "--secret" not in cmd            # never on argv
    assert cmd[cmd.index("--evaluation-id") + 1] == "local"


def test_eval_batch_defaults_secret_to_public(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    calls = []
    eval_batch(sol, _SPEC, "img:dev", now="2026-09-03T00:00:00Z",
               runner=_make_fake_docker_run(calls),
               image_digest_resolver=lambda img: "img@sha256:deadbeef")
    assert "NETHACK_ARENA_SECRET=public" in calls[0]
