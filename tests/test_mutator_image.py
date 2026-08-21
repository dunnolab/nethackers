"""Docker-gated acceptance tests for the mutator sandbox image
(Dockerfile.mutator) -- the kill-switch acceptance criteria from
docs/superpowers/specs/2026-08-16-mutator-sandbox-design.md §4: a fork bomb
dies at the container's ``--pids-limit`` with the host unharmed, ``rm -rf /``
is scoped to the (disposable, ``--rm``) container, no host secret is
reachable (nothing sensitive is ever mounted), and both harness CLIs the
``ContainerOperator`` wraps (harness/container_operator.py) are present and
runnable inside the image, alongside the NLE + gymnasium the mutator
experiments against.

Collection-safe like tests/test_docker_smoke.py: only stdlib + pytest are
imported at module scope, and the only thing that runs at collection time is
a cheap ``shutil.which("docker")`` PATH check (no subprocess) -- so this file
collects cleanly with no Docker daemon at all. Whether the image is actually
*built* is checked lazily, once per test, via an autouse fixture -- that
keeps a routine `pytest -m "not docker"` run (which deselects every test
here before setup ever runs) from ever shelling out to `docker image
inspect`. A missing image reads as a clean skip, not a wall of docker
"no such image" failures.

Excluded from the routine run via the `docker` marker (`uv run pytest -m
"not nle and not docker and not claude_live"`); run directly with `uv run
pytest tests/test_mutator_image.py -v -m docker` after `make mutator`.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

IMAGE = "nethackers/mutator:latest"

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not shutil.which("docker"), reason="docker required"),
]


def _image_built(image: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@pytest.fixture(autouse=True)
def _require_image() -> None:
    if not _image_built(IMAGE):
        pytest.skip(f"{IMAGE} not built locally -- run `make mutator` (needs `make arena` first)")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "run", "--rm", *args], capture_output=True, text=True, timeout=120,
    )


def test_nle_and_python_present() -> None:
    r = _run(["--entrypoint", "python", IMAGE, "-c", "import nle, gymnasium; print('ok')"])
    assert "ok" in r.stdout


def test_forkbomb_dies_at_pids_limit_host_unharmed() -> None:
    # ":(){ :|:& };:" backgrounds its own recursive explosion, so the
    # *invoking* `bash -c` script's own next commands (`sleep 2; echo
    # survived`) run regardless of whether the explosion itself is capped --
    # empirically, "survived" prints either way, so its presence alone is
    # NOT evidence the cage held (verified against this exact image: rc=0,
    # "survived" in stdout, even though the cap demonstrably engaged). What
    # --pids-limit actually does is make every fork() past the cap fail
    # (EAGAIN) instead of exponentially succeeding; bash's own diagnostic
    # for that ("fork: retry: Resource temporarily unavailable") is the
    # direct, unambiguous proof the cage rejected the runaway forking
    # instead of letting it explode across the host. The container
    # finishing at all inside the 120s timeout -- not hanging, not wedging
    # `docker run` itself -- is the "host unharmed" half.
    r = _run(["--pids-limit", "64", "--memory", "512m", "--entrypoint", "bash", IMAGE,
              "-c", ":(){ :|:& };: ; sleep 2; echo survived"])
    assert "survived" in r.stdout  # the invoking shell itself was never killed
    assert "Resource temporarily unavailable" in r.stderr  # the cap rejected the runaway forks


def test_rm_rf_root_scoped_to_container() -> None:
    # `--rm` makes the container disposable; the destructive command runs
    # entirely inside its own (ephemeral, copy-on-write) filesystem layer --
    # nothing outside the container is ever touched. Prove it structurally:
    # after `rm -rf /` finishes (whatever its own exit code), the image on
    # disk is untouched and a brand-new container from it still starts up
    # cleanly -- the destruction did not escape the container it ran in.
    wreckage = _run(["--entrypoint", "bash", IMAGE, "-c", "rm -rf / 2>/dev/null; echo done-$?"])
    assert "done-" in wreckage.stdout

    survivor = _run(["--entrypoint", "python", IMAGE, "-c", "print('still alive')"])
    assert "still alive" in survivor.stdout


def test_no_host_secrets_reachable() -> None:
    r = _run(["--entrypoint", "bash", IMAGE, "-c", "cat ~/.ssh/id_* 2>&1 | head -1"])
    assert "No such file" in r.stdout or "No such file" in r.stderr


def test_harness_clis_present() -> None:
    for cli in ("claude", "codex"):
        r = _run(["--entrypoint", cli, IMAGE, "--version"])
        assert r.returncode == 0
