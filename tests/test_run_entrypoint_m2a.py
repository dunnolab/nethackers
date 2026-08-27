"""Exercises ``run.py``'s ``--batch`` CLI wiring end-to-end (``main()``, via
an injected ``run_batch`` fake) and the concurrent fan-out logic itself
(``run_batch``/``_failed_result``, via an injected ``run_one`` +
``executor_factory=ThreadPoolExecutor``).

``main()``'s real path uses a ``ProcessPoolExecutor`` for crash isolation
(see the module docstring in ``nethackers/arena/run.py``), which cannot run
a test-local closure (closures aren't picklable, and a worker process
re-imports modules fresh so it would never see a parent-process monkeypatch
either way). So the per-entry construction logic -- the headline character
dual-threading, the ``"-"`` random-draw sentinel, batch-order preservation,
dead-worker isolation -- is exercised by calling ``run_batch`` directly with
an injected thread pool instead, matching this file's fake-injection style
throughout.

Nothing here touches NLE, a real sandboxed bot subprocess, or the Docker
image (see tests/test_docker_smoke.py for that). ``--batch`` is the only
supported way in -- the legacy single-character ``--character``/``--seeds``
path has been retired.
"""

import concurrent.futures as cf
import json

import pytest

import nethackers.arena.run as R
from nethackers.arena import run as run_mod
from nethackers.arena.run import run_batch
from nethackers.contracts.models import TrajectoryResult


def _fake(tid, char, progress=0.5, status="completed"):
    return TrajectoryResult(
        trajectory_id=tid, status=status, progress=progress, ascended=False,
        steps=1, turns=1, max_depth=1, end_status=None, error=None,
        wall_seconds=0.0, character=char, milestone=None)


def test_batch_threads_character_into_objective_and_result():
    """The headline fix, exercised at ``run_batch`` -- where per-entry
    Objective/character construction now lives: each batch entry's character
    drives BOTH ``objective.character`` (env's NLE build selection) AND the
    ``character=`` kwarg (result identity). A future edit that drops
    ``character=`` from the ``run_one`` call would fail this assertion
    (KeyError) even though ``objective.character`` is right."""
    calls = {}

    def fake_run_one(submission_path, spec, objective, character):
        calls[spec.trajectory_id] = {"objective": objective, "character": character}
        return _fake(spec.trajectory_id, character)

    results = run_batch(
        "/sol", [[0, "val-dwa-law-fem"], [3, "wiz-elf-cha-mal"]],
        secret="public", evaluation_id="e", max_steps=100, no_progress_timeout=10,
        action_timeout=5.0, max_parallel_evals=8,
        run_one=fake_run_one, executor_factory=cf.ThreadPoolExecutor)

    assert len(calls) == 2

    # The headline fix: each entry's character drives BOTH objective.character
    # (env's NLE build selection) AND the character= kwarg (result identity).
    assert calls[0]["objective"].character == "val-dwa-law-fem"
    assert calls[0]["character"] == "val-dwa-law-fem"
    assert calls[3]["objective"].character == "wiz-elf-cha-mal"
    assert calls[3]["character"] == "wiz-elf-cha-mal"

    # Results: one per batch entry, in batch order (index-keyed by
    # run_batch, not completion order).
    assert [r.trajectory_id for r in results] == [0, 3]


def test_batch_dash_sentinel_means_random_draw_but_is_still_recorded():
    calls = {}

    def fake_run_one(submission_path, spec, objective, character):
        calls[spec.trajectory_id] = {"objective": objective, "character": character}
        return _fake(spec.trajectory_id, character)

    run_batch(
        "/sol", [[7, "-"]], secret="public", evaluation_id="e", max_steps=100,
        no_progress_timeout=10, action_timeout=5.0, max_parallel_evals=8,
        run_one=fake_run_one, executor_factory=cf.ThreadPoolExecutor)

    # "-" configures the environment for NLE's natural random draw...
    assert calls[7]["objective"].character is None
    # ...but the sentinel itself is still passed through as the recorded
    # identity (run_batch records the literal batch entry, not a resolved build).
    assert calls[7]["character"] == "-"


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


def test_evaluation_id_defaults_to_local_when_omitted():
    """Root cause ①: the judge hardcodes --evaluation-id local (eval/runner.py).
    A mutator self-eval that omits the flag must play the judge's dungeons, not
    an invented namespace -- so the default has to match the judge exactly."""
    from nethackers.arena.run import _parser
    args = _parser().parse_args(
        ["--solution", "/sol", "--batch", "[]", "--out", "/out/x.json"])
    assert args.evaluation_id == "local"   # matches the judge (eval/runner.py passes "local")


def test_action_timeout_default_is_the_local_hang_guard():
    """Root cause ③: the arena's per-action wall-clock budget is a HANG-GUARD,
    not a scoring knob. The LOCAL default is a generous 120s so a normal action
    (incl. a cold numba JIT compile under parallel load) is never cut -- only a
    genuinely hung bot times out. The validator overrides --action-timeout with
    its own value; it must not inherit this local default."""
    from nethackers.arena.run import _parser
    args = _parser().parse_args(
        ["--solution", "/sol", "--batch", "[]", "--out", "/out/x.json"])
    assert args.action_timeout == 120.0


def test_main_passes_knob_and_writes_batch_order(tmp_path, monkeypatch):
    calls = {}

    def fake_run_batch(submission_path, batch, **kw):
        calls.update(kw)
        calls["batch"] = batch
        return [TrajectoryResult(trajectory_id=int(s), status="completed", progress=0.1 * k,
                ascended=False, steps=1, turns=1, max_depth=1, end_status=None, error=None,
                wall_seconds=0.0, character=c, milestone=None)
                for k, (s, c) in enumerate(batch)]

    monkeypatch.setattr(run_mod, "run_batch", fake_run_batch)
    out = tmp_path / "results.json"
    rc = run_mod.main([
        "--solution", "/sol",
        "--batch", json.dumps([[0, "tou-hum-neu-mal"], [1, "tou-hum-neu-mal"]]),
        "--evaluation-id", "local", "--out", str(out), "--max-parallel-evals", "4"])
    assert rc == 0
    assert calls["max_parallel_evals"] == 4
    written = json.loads(out.read_text())
    assert [r["trajectory_id"] for r in written] == [0, 1]  # batch order
