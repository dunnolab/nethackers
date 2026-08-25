"""Exercises ``nethackers.eval.runner.eval_batch``'s docker-command
construction and Evidence-wrapping for an ``ObjectiveSpec`` batch, with a
fake docker runner and an injected image-digest resolver -- no real Docker
daemon, no NLE, and no shelling out to `docker image inspect` (that call
lives only in ``_default_image_digest``, which this module never exercises
-- it is a thin docker shell, covered only via the injected resolver here).

Mirrors tests/test_eval_runner.py's fake-runner pattern: the fake never
launches a container, it locates the host directory bind-mounted at
``/out`` from the ``cmd`` list ``eval_batch`` builds, and writes a
results.json there itself -- one dict per batch episode, in batch order.
"""

import json
from pathlib import Path

from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import eval_batch

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
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{sol}:/sol:ro" in cmd
    expected_batch_arg = json.dumps([[seed, char] for seed, char in _SPEC.batch])
    assert cmd[cmd.index("--batch") + 1] == expected_batch_arg
    assert "--character" not in cmd
    assert "--seeds" not in cmd


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
