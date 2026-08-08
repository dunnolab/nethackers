"""Exercises ``nethackers.cli.main``'s subcommand wiring for ``eval`` and
``pull`` with both ``run_eval`` and ``pull`` monkeypatched on the ``cli``
module -- no real Docker or git process is ever invoked here.
"""

import json

import nethackers.cli as C
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult


def test_cli_eval_invokes_runner(monkeypatch, capsys, tmp_path):
    def fake(solution, objective, image, *, seed_ids, now):
        assert isinstance(objective, Objective)
        assert seed_ids == [0]
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "run_eval", fake)

    rc = C.main(["eval", str(tmp_path), "--character", "val-dwa-law-fem", "--seeds", "0"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mean_progress"] == 0.1
    assert out["evaluator_image"] == "nethackers/arena:dev"


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
