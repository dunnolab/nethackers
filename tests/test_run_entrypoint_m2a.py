"""Exercises the ``--batch`` branch of ``run.py``'s CLI wiring: one
``run_trajectory`` call per batch entry in batch order, the headline
dual-threading (each entry's character drives BOTH ``objective.character``
and the ``character=`` kwarg recorded on the result), the ``"-"``
random-draw sentinel, the ``--batch``-or-``--seeds`` guard, and the JSON
results file.

`nethackers.arena.trajectory.run_trajectory` is monkeypatched with a fake
that records every call's kwargs and returns a stub whose ``.to_dict()``
echoes back an identifying dict, so this covers only `main()`'s own
plumbing -- not NLE, not a real sandboxed bot subprocess, and not the Docker
image (see tests/test_docker_smoke.py for that). ``--batch`` is now the
only supported way in -- the legacy single-character ``--character``/
``--seeds`` path has been retired.
"""

import concurrent.futures as cf
import json

import pytest

import nethackers.arena.run as R
from nethackers.arena.run import run_batch
from nethackers.contracts.models import TrajectoryResult


def _fake_run_trajectory(calls):
    """Record every call's kwargs and return a stub whose ``to_dict()``
    echoes back the call's ``spec.trajectory_id`` -- enough to check
    per-entry ordering without a real trajectory run."""

    def fake(**kwargs):
        calls.append(kwargs)
        trajectory_id = kwargs["spec"].trajectory_id
        # Stub stands in for a real TrajectoryResult: to_dict() for the results
        # file, plus the fields run.py now reads for its per-episode stderr line.
        return type(
            "T",
            (),
            {
                "to_dict": lambda self: {"trajectory_id": trajectory_id},
                "progress": 0.1,
                "status": "completed",
                "turns": 1,
                "max_depth": 1,
            },
        )()

    return fake


def test_batch_threads_character_into_objective_and_result(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(R, "run_trajectory", _fake_run_trajectory(calls))
    out = tmp_path / "r.json"

    rc = R.main(
        [
            "--solution",
            str(tmp_path),
            "--batch",
            json.dumps([[0, "val-dwa-law-fem"], [3, "wiz-elf-cha-mal"]]),
            "--evaluation-id",
            "e",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    assert len(calls) == 2

    # One call per batch entry, in batch order.
    assert calls[0]["spec"].trajectory_id == 0
    assert calls[1]["spec"].trajectory_id == 3

    # The headline fix: each entry's character drives BOTH objective.character
    # (env's NLE build selection) AND the character= kwarg (result identity).
    # A future edit that drops character= from the run_trajectory call would
    # fail this assertion (KeyError) even though objective.character is right.
    assert calls[0]["objective"].character == "val-dwa-law-fem"
    assert calls[0]["character"] == "val-dwa-law-fem"
    assert calls[1]["objective"].character == "wiz-elf-cha-mal"
    assert calls[1]["character"] == "wiz-elf-cha-mal"

    # Results JSON: one dict per batch entry, in order.
    results = json.loads(out.read_text())
    assert len(results) == 2
    assert [r["trajectory_id"] for r in results] == [0, 3]


def test_batch_dash_sentinel_means_random_draw_but_is_still_recorded(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(R, "run_trajectory", _fake_run_trajectory(calls))
    out = tmp_path / "r.json"

    rc = R.main(
        [
            "--solution",
            str(tmp_path),
            "--batch",
            json.dumps([[7, "-"]]),
            "--evaluation-id",
            "e",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    # "-" configures the environment for NLE's natural random draw...
    assert calls[0]["objective"].character is None
    # ...but the sentinel itself is still passed through as the recorded
    # identity (run.py records the literal batch entry, not a resolved build).
    assert calls[0]["character"] == "-"


def test_neither_batch_nor_seeds_raises_systemexit(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        R.main(
            [
                "--solution",
                str(tmp_path),
                "--evaluation-id",
                "e",
                "--out",
                str(tmp_path / "r.json"),
            ]
        )
    assert excinfo.value.code == 2  # argparse's parser.error() exit status


def _fake(tid, char, progress=0.5, status="completed"):
    return TrajectoryResult(
        trajectory_id=tid, status=status, progress=progress, ascended=False,
        steps=1, turns=1, max_depth=1, end_status=None, error=None,
        wall_seconds=0.0, character=char, milestone=None)


def test_run_batch_preserves_order_and_streams_true_index():
    batch = [[0, "tou-hum-neu-mal"], [1, "tou-hum-neu-mal"], [2, "tou-hum-neu-mal"]]
    seen = []

    def fake_run_one(submission_path, spec, objective, character):
        return _fake(spec.trajectory_id, character, progress=spec.trajectory_id / 10)

    results = run_batch(
        "/sol", batch, secret="public", evaluation_id="local", max_steps=100,
        no_progress_timeout=10, action_timeout=5.0, max_parallel_evals=3,
        on_episode=lambda i, r: seen.append(i),
        run_one=fake_run_one, executor_factory=cf.ThreadPoolExecutor)

    assert [r.progress for r in results] == [0.0, 0.1, 0.2]  # batch order preserved
    assert sorted(seen) == [0, 1, 2]                          # every index streamed
    assert len(results) == 3


def test_run_batch_caps_parallelism_at_batch_size():
    captured = {}

    class SpyExec(cf.ThreadPoolExecutor):
        def __init__(self, max_workers):
            captured["P"] = max_workers
            super().__init__(max_workers=max_workers)

    run_batch("/sol", [[0, "x"]], secret="s", evaluation_id="e", max_steps=1,
              no_progress_timeout=1, action_timeout=1.0, max_parallel_evals=8,
              run_one=lambda *a: _fake(0, "x"), executor_factory=SpyExec)
    assert captured["P"] == 1


def test_run_batch_isolates_a_dead_worker():
    batch = [[0, "x"], [1, "x"]]

    def flaky(submission_path, spec, objective, character):
        if spec.trajectory_id == 1:
            raise RuntimeError("worker segfault")
        return _fake(spec.trajectory_id, character)

    results = run_batch("/sol", batch, secret="s", evaluation_id="e", max_steps=1,
                        no_progress_timeout=1, action_timeout=1.0, max_parallel_evals=2,
                        run_one=flaky, executor_factory=cf.ThreadPoolExecutor)
    assert results[0].status == "completed"
    assert results[1].status == "infrastructure_error" and results[1].progress == 0.0
    assert len(results) == 2
