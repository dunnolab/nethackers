"""Docker-compose smoke test for the hub stack (M2a Task 14): builds and
runs the whole hub via `docker compose up -d --build` (hub/Dockerfile +
compose.yaml, both at the repo root), polls `GET /objectives` until it
answers, checks the fixture-seeded catalog comes back non-empty, then tears
the stack down. See task-14-context.md, which governs.

Collection-safe, mirroring tests/test_docker_smoke.py: only stdlib +
pytest are imported at module scope (no docker SDK, no requests), and no
subprocess is launched until the test body runs -- so this file collects
cleanly even without a Docker daemon. Excluded from the routine run via
the `docker` marker (`uv run pytest -m "not nle and not docker"`); the
controller runs it separately (`uv run pytest -m docker`).
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
BASE_URL = "http://localhost:8000"
POLL_ATTEMPTS = 60
POLL_INTERVAL_SECONDS = 2.0


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _wait_for_objectives() -> list[dict[str, object]]:
    """Poll ``GET /objectives`` until it answers 200, up to
    ``POLL_ATTEMPTS`` tries -- the container needs a moment to install deps
    on first build and start uvicorn. Raises ``TimeoutError`` (with the
    last connection error attached) if it never comes up."""
    last_error: Exception | None = None
    for _ in range(POLL_ATTEMPTS):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/objectives", timeout=5) as resp:
                if resp.status == 200:
                    result: list[dict[str, object]] = json.loads(resp.read())
                    return result
        except (urllib.error.URLError, OSError) as e:
            last_error = e
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"hub API never became ready at {BASE_URL}/objectives: {last_error}")


@pytest.mark.docker
def test_compose_up_serves_fixture_seeded_objectives() -> None:
    try:
        build = _compose("up", "-d", "--build")
        assert build.returncode == 0, build.stderr

        objectives = _wait_for_objectives()

        assert isinstance(objectives, list)
        assert objectives != []
        names = {o["name"] for o in objectives}
        assert "random" in names  # the headline objective every fixture load publishes
    finally:
        teardown = _compose("down", "-v")
        assert teardown.returncode == 0, teardown.stderr
