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
image (see tests/test_docker_smoke.py for that). Mirrors
tests/test_run_entrypoint.py's pattern, which covers the legacy
``--character``/``--seeds`` path.
"""

import json

import pytest

import nethackers.arena.run as R


def _fake_run_trajectory(calls):
    """Record every call's kwargs and return a stub whose ``to_dict()``
    echoes back the call's ``spec.trajectory_id`` -- enough to check
    per-entry ordering without a real trajectory run."""

    def fake(**kwargs):
        calls.append(kwargs)
        trajectory_id = kwargs["spec"].trajectory_id
        return type("T", (), {"to_dict": lambda self: {"trajectory_id": trajectory_id}})()

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
