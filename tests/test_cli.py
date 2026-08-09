"""Exercises ``nethackers.cli.main``'s subcommand wiring for ``eval`` and
``pull`` with both ``eval_batch`` and ``pull`` monkeypatched on the ``cli``
module -- no real Docker or git process is ever invoked here.

``eval`` resolves ``--objective <name>`` against the real
``nethackers.hub.objectives.CATALOG`` and calls ``eval_batch`` with the
resolved ``ObjectiveSpec`` -- the loop-closing regression proving that
evidence is actually *registerable* lives in tests/test_cli_m2a.py
alongside the rest of the M2a hub-facing CLI coverage.
"""

import json

import nethackers.cli as C
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.objectives import CATALOG


def test_cli_eval_invokes_eval_batch_with_resolved_objective(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_eval_batch(solution, spec, image, *, now):
        seen["solution"] = solution
        seen["spec"] = spec
        seen["image"] = image
        seen["now"] = now
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(["eval", str(tmp_path), "--objective", "val-dwa-law-fem"])

    assert rc == 0
    # Resolves the real catalog entry -- not a hand-rolled Objective.
    assert seen["spec"] is CATALOG["val-dwa-law-fem"]
    assert seen["solution"] == tmp_path
    assert seen["image"] == "nethackers/arena:dev"

    out = json.loads(capsys.readouterr().out)
    assert out["mean_progress"] == 0.1
    assert out["evaluator_image"] == "nethackers/arena:dev"
    assert out["objective"]["seed_set"] == "val-dwa-law-fem"


def test_cli_eval_custom_image_is_passed_through(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_eval_batch(solution, spec, image, *, now):
        seen["image"] = image
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(
        ["eval", str(tmp_path), "--objective", "random", "--image", "custom/arena:tag"]
    )

    assert rc == 0
    assert seen["image"] == "custom/arena:tag"


def test_cli_eval_unknown_objective_errors_without_traceback(capsys, tmp_path):
    rc = C.main(["eval", str(tmp_path), "--objective", "not-a-real-objective"])

    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing printed to stdout
    assert "not-a-real-objective" in captured.err


def test_cli_pull_invokes_pull(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_pull(repo_at_commit, dest):
        seen["repo_at_commit"] = repo_at_commit
        seen["dest"] = dest
        return dest

    monkeypatch.setattr(C, "pull", fake_pull)
    dest = tmp_path / "d"

    rc = C.main(["pull", "dunnolab/nethacker@abc123", str(dest)])

    assert rc == 0
    assert seen == {"repo_at_commit": "dunnolab/nethacker@abc123", "dest": dest}
    assert capsys.readouterr().out.strip() == str(dest)
