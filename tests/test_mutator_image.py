"""Docker-gated acceptance tests for the mutator sandbox image
(Dockerfile.mutator) -- the kill-switch acceptance criteria: a fork bomb
dies at the container's ``--pids-limit`` with the host unharmed, ``rm -rf /``
is scoped to the (disposable, ``--rm``) container, no host secret is
reachable (nothing sensitive is ever mounted), both harness CLIs the
``ContainerOperator`` wraps (harness/container_operator.py) are present and
runnable inside the image, and -- through the real (non-``--entrypoint``-
overridden) entrypoint -- the container actually drops root to the non-root
``agent`` user via docker-entrypoint.sh's ``gosu`` handoff, alongside the
NLE + gymnasium the mutator experiments against.

Most of these tests pass ``--entrypoint`` to isolate one specific check,
which bypasses docker-entrypoint.sh and runs as root -- fine for what each
of *those* checks is verifying, but it means none of them alone proves the
image's headline non-root requirement.
``test_default_entrypoint_drops_root_to_agent`` is the one test that invokes
the image exactly as ``ContainerOperator`` does (no ``--entrypoint``), so
the real ``gosu``-drop actually runs; ``test_no_host_secrets_reachable``
deliberately reuses that same real path so ``~`` resolves against the
actual runtime home (``/home/agent``, where ``harness/auth_inject.py``
lands its mounts) instead of root's unrelated ``/root``.

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
pytest tests/test_mutator_image.py -v -m docker` after `nethackers doctor
--pull`, which pulls or builds the mutator this checkout needs.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from nethackers.harness.sandbox_preflight import resolve_image

# The mutator this checkout resolves to: its fingerprint image, or the pin.
IMAGE = resolve_image(None, "mutator")

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
        pytest.skip(f"{IMAGE} not built locally -- run `nethackers doctor --pull`")


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


def test_default_entrypoint_drops_root_to_agent() -> None:
    # Every other test in this file passes --entrypoint, which bypasses
    # docker-entrypoint.sh entirely and runs as root -- none of them exercise
    # the actual non-root handoff. This is the one test that invokes the
    # image exactly as ContainerOperator does (build_docker_argv never
    # passes --entrypoint -- see harness/container_operator.py), so the real
    # `gosu`-drop + PUID/PGID auto-detect path in docker-entrypoint.sh
    # actually runs. Guards against a future edit silently deleting the
    # `gosu` line and regressing the image back to root-by-default, which
    # would also break Claude Code (it hard-refuses
    # --dangerously-skip-permissions as root).
    #
    # --security-opt no-new-privileges is included because build_docker_argv
    # ALWAYS sets it in production (container_operator.py) -- the gosu
    # root→agent drop is a privilege *reduction*, which no-new-privileges
    # does not block, but this test should exercise the real flag rather
    # than a friendlier stand-in that happens to also pass.
    r = _run(["--security-opt", "no-new-privileges", IMAGE, "whoami"])
    assert "agent" in r.stdout

    r = _run(["--security-opt", "no-new-privileges", IMAGE, "id", "-u"])
    assert "1000" in r.stdout


def test_opencode_xdg_dirs_are_writable_after_uid_remap() -> None:
    # The entrypoint remaps agent to the workspace owner's uid/gid. Directory
    # ownership baked as 1000:1000 must follow that remap without recursively
    # touching any bind-mounted config/auth files.
    r = _run([
        "-e", "PUID=12345", "-e", "PGID=12345", IMAGE, "sh", "-c",
        "mkdir -p ~/.local/state/opencode ~/.local/share/opencode "
        "~/.cache/opencode ~/.config/opencode && "
        "touch ~/.local/state/opencode/write-test "
        "~/.local/share/opencode/write-test ~/.cache/opencode/write-test && echo WRITABLE",
    ])
    assert r.returncode == 0, r.stderr
    assert "WRITABLE" in r.stdout


def test_no_host_secrets_reachable() -> None:
    # No --entrypoint override here either, deliberately: the real runtime
    # identity is `agent` (home /home/agent -- confirmed by the previous
    # test, and where harness/auth_inject.py actually lands its .codex/
    # .claude mounts), not root's unrelated /root that --entrypoint bash
    # would otherwise resolve `~` against. `bash -c` is still needed since
    # there's no --entrypoint override to swap the default `bash` shell in
    # for something else, and `~` needs a shell to expand.
    r = _run([IMAGE, "bash", "-c", "cat ~/.ssh/id_* 2>&1 | head -1"])
    assert "No such file" in r.stdout or "No such file" in r.stderr


def test_harness_clis_present() -> None:
    for cli in ("claude", "codex", "opencode2"):
        r = _run(["--entrypoint", cli, IMAGE, "--version"])
        assert r.returncode == 0


def test_info_diet_wall_no_seeds_no_cli() -> None:
    """The info-diet wall (spec §3.6) is the image boundary: the mutator must
    NOT be able to derive the held-out seeds or reach the nethackers CLI. It
    only carries the seed-free scoring kit (arena + contracts) for parity."""
    # the seed formula (harness/seeds.py: validation_spec start=1000) is absent
    leak = _run(["--entrypoint", "python", IMAGE, "-c",
                 "import nethackers.harness.seeds"])
    assert leak.returncode != 0
    assert "ModuleNotFoundError" in leak.stderr or "No module" in leak.stderr
    # no `nethackers` CLI on PATH
    cli = _run(["--entrypoint", "bash", IMAGE, "-c", "command -v nethackers || echo ABSENT"])
    assert "ABSENT" in cli.stdout
    # but the seed-free scoring kit IS present (parity self-testing)
    kit = _run(["--entrypoint", "python", IMAGE, "-c",
                "import nethackers.arena.progress, nethackers.contracts.models; print('KIT')"])
    assert "KIT" in kit.stdout
