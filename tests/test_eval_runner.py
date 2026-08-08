"""Exercises ``nethackers.eval.runner.eval``'s docker-command construction
and Evidence-wrapping with a fake docker runner -- no real Docker daemon and
no NLE are involved. The fake never launches a container: it reads the
``cmd`` list ``eval`` builds, locates the host directory bind-mounted at
``/out`` (mirroring how a real ``docker run`` of the arena image would
populate it, per ``arena/run.py``'s own ``--out`` handling), and writes a
results.json there itself.
"""

import json
from pathlib import Path

from nethackers.contracts.models import Objective
from nethackers.eval.runner import eval as run_eval

_RESULT = {
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
}


def _make_fake_docker_run(calls):
    """Build a fake ``runner``: records the ``cmd`` it was called with, then
    -- instead of launching a container -- writes a results.json directly
    into the host directory the real docker command would have bind-mounted
    at /out (found via the "-v <host>:/out" argument)."""

    def fake(cmd, check):
        calls.append(cmd)
        assert check is True
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps([_RESULT]))

    return fake


def test_eval_wraps_container_results_into_evidence(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    obj = Objective("val-dwa-law-fem", 200, 200, 5.0, "public-1")
    calls = []

    ev = run_eval(
        sol, obj, "img:dev", seed_ids=[0], now="2026-08-08T00:00:00Z",
        runner=_make_fake_docker_run(calls),
    )

    assert ev.episodes == 1
    assert ev.mean_progress == 0.5
    assert ev.tier == "self-reported"
    assert ev.evaluator_image == "img:dev"
    assert ev.solution_digest.startswith("sha256:")

    # The command actually built: --network none, solution mounted read-only,
    # and the character/seeds threaded through from the Objective/seed_ids.
    cmd = calls[0]
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{sol}:/sol:ro" in cmd
    assert cmd[cmd.index("--character") + 1] == "val-dwa-law-fem"
    assert cmd[cmd.index("--seeds") + 1] == "0"


def test_eval_uses_dash_placeholder_when_character_is_none(tmp_path):
    """objective.character is None => the container's "-" sentinel is passed
    (run.py: ``--character "-"`` => ``character = None``)."""
    sol = tmp_path / "sol"
    sol.mkdir()
    obj = Objective(None, 200, 200, 5.0, "public-1")
    calls = []

    run_eval(
        sol, obj, "img:dev", seed_ids=[0], now="2026-08-08T00:00:00Z",
        runner=_make_fake_docker_run(calls),
    )

    cmd = calls[0]
    assert cmd[cmd.index("--character") + 1] == "-"
