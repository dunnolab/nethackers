"""Exercise run.py's CLI wiring: argument parsing, `--character "-"` => None,
one `run_trajectory` call per `--seeds` id, and the JSON results file.

`nethackers.arena.trajectory.run_trajectory` is monkeypatched with a fake
that echoes back each call's `spec.trajectory_id`, so this covers only
`main()`'s own plumbing -- not NLE, not a real sandboxed bot subprocess, and
not the Docker image (see tests/test_docker_smoke.py for that).
"""

import json

import nethackers.arena.run as R


def test_run_parses_args_without_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        R,
        "run_trajectory",
        lambda **k: type(
            "T", (), {"to_dict": lambda self: {"trajectory_id": k["spec"].trajectory_id}}
        )(),
    )
    out = tmp_path / "r.json"
    rc = R.main(
        [
            "--solution",
            str(tmp_path),
            "--character",
            "-",
            "--seeds",
            "0,1",
            "--evaluation-id",
            "e",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    results = json.loads(out.read_text())
    assert len(results) == 2
    assert [r["trajectory_id"] for r in results] == [0, 1]
