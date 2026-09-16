"""Docker smoke test for the pinned arena image (arena/Dockerfile).

Builds no image itself -- requires `nethackers/arena:dev` to already exist
locally (e.g. `docker build -t nethackers/arena:dev -f arena/Dockerfile .`)
and a working Docker daemon. Runs the AutoAscend flagship solution for one
trajectory through `docker run` exactly as described in the Task 9 brief,
writing the result to /dev/stdout, and checks that stdout is a parseable,
non-empty `TrajectoryResult.to_dict()` JSON array.

Collection-safe: only stdlib + pytest are imported at module scope. Nothing
here imports docker or nle, and no subprocess is launched until the test
body runs, so this file collects cleanly even without Docker or NLE
installed -- it is excluded from routine runs via the `docker` marker
(`uv run pytest -m "not nle and not docker"`).
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
SOLUTION = REPO_ROOT / "src" / "nethackers" / "roots" / "autoascend"
IMAGE = "nethackers/arena:dev"


@pytest.mark.docker
def test_docker_image_runs_one_trajectory_to_stdout():
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{SOLUTION}:/sol:ro",
            IMAGE,
            "--solution",
            "/sol",
            "--batch",
            json.dumps([[0, "val-dwa-law-fem"]]),
            "--evaluation-id",
            "smoke",
            "--max-steps",
            "200",
            "--out",
            "/dev/stdout",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr

    results = json.loads(proc.stdout)

    assert isinstance(results, list)
    assert len(results) == 1
    result = results[0]
    assert result["trajectory_id"] == 0
    assert "progress" in result
    assert isinstance(result["progress"], float)
