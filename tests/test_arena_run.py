"""Exercises ``nethackers.arena.run.main``'s TWO entry paths (Task 7, threat
3 a,b / INV3):

- the new stdin path: ``eval_batch`` (the trusted host eval) derives the
  concrete per-trajectory ``TrajectorySpec``s host-side and pipes them in as
  JSON -- this process never sees a secret, on argv or in the environment.
- the pre-existing ``--batch``/``--secret``/``--evaluation-id`` path (R67-2):
  kept as a public-seed fallback because ``harness/brief.py`` documents it as
  the mutator's in-container self-test.

An injected ``run_one`` (never the real ``run_trajectory``) plus
``executor_factory=ThreadPoolExecutor`` keeps this NLE-free and avoids the
spawn-pickling hazard ``tests/test_run_entrypoint_m2a.py`` documents for a
test-local ``run_one`` under the real (``ProcessPoolExecutor``) default.
"""

import concurrent.futures as cf
import io
import json

from nethackers.arena import run as arena_run
from nethackers.contracts.models import TrajectoryResult


def fake_run_one(submission_path, spec, objective, character):
    """Module-level (not a closure) to mirror the injection style
    ``run_batch``/``run_prepared`` already support -- irrelevant for
    ``ThreadPoolExecutor``, which needs no pickling, but keeping this
    top-level avoids relying on that distinction at all."""
    return TrajectoryResult(
        trajectory_id=spec.trajectory_id, status="completed", progress=0.5,
        ascended=False, steps=1, turns=1, max_depth=1, end_status=None, error=None,
        wall_seconds=0.0, character=character, milestone=None)


class _ExplodingStdin:
    """Raises on ``.read()`` -- so a test using this fails loudly if the code
    under test ever reads stdin when it shouldn't."""

    def read(self) -> str:
        raise AssertionError("must not read stdin on this path")


def test_main_reads_specs_from_stdin(monkeypatch, tmp_path):
    specs = [
        {
            "spec": {"trajectory_id": 0, "core_seed": 1, "display_seed": 2,
                      "level_seed": 3, "bot_seed": 4},
            "character": "val-dwa-law-fem",
        },
        {
            "spec": {"trajectory_id": 7, "core_seed": 11, "display_seed": 12,
                      "level_seed": 13, "bot_seed": 14},
            "character": "wiz-elf-cha-mal",
        },
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(specs)))
    out = tmp_path / "r.json"

    rc = arena_run.main(
        ["--solution", str(tmp_path), "--out", str(out)],
        run_one=fake_run_one, executor_factory=cf.ThreadPoolExecutor,
    )

    assert rc == 0
    assert out.exists()
    written = json.loads(out.read_text())
    # Batch order preserved, and the pre-derived spec's trajectory_id/
    # character both threaded through -- no --batch, no secret, involved.
    assert [r["trajectory_id"] for r in written] == [0, 7]
    assert [r["character"] for r in written] == ["val-dwa-law-fem", "wiz-elf-cha-mal"]


def test_batch_fallback_still_works_without_stdin(monkeypatch, tmp_path):
    """R67-2: the mutator's in-container self-test (harness/brief.py) always
    passes ``--batch``/``--evaluation-id local`` on PUBLIC seeds, and must
    keep working exactly as before. The exploding stdin fake also proves
    this path never attempts to read stdin at all."""
    monkeypatch.setattr("sys.stdin", _ExplodingStdin())
    out = tmp_path / "r.json"

    rc = arena_run.main(
        [
            "--solution", str(tmp_path),
            "--batch", json.dumps([[0, "val-dwa-law-fem"], [1, "wiz-elf-cha-mal"]]),
            "--evaluation-id", "local",
            "--out", str(out),
        ],
        run_one=fake_run_one, executor_factory=cf.ThreadPoolExecutor,
    )

    assert rc == 0
    written = json.loads(out.read_text())
    assert [r["trajectory_id"] for r in written] == [0, 1]
    assert [r["character"] for r in written] == ["val-dwa-law-fem", "wiz-elf-cha-mal"]
