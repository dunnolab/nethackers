import re
from types import SimpleNamespace

from nethackers.containers import NETHACKERS_LABEL, container_name, container_runtime, label_args


def test_container_name_prefixed_and_unique():
    a, b = container_name("arena"), container_name("arena")
    assert re.fullmatch(r"nethackers-arena-[0-9a-f]{8}", a)
    assert a != b  # the hex suffix keeps concurrent containers unique


def test_label_args_and_label_constant():
    assert NETHACKERS_LABEL == "nethackers"
    assert label_args() == ["--label", "nethackers"]


# --- container_runtime: which docker/podman binary is actually usable -------
# issue #50: nethackers only ever ran the `docker` binary, so a machine with a
# working rootless podman (and no docker binary / a shell-only `alias
# docker=podman`, which no subprocess can see) reported "no container runtime".


def _which_only(*names):
    have = set(names)
    return lambda name: (f"/usr/bin/{name}" if name in have else None)


def test_container_runtime_prefers_docker_when_both_are_usable(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_only("docker", "podman"))
    rt = container_runtime(run=lambda *a, **k: SimpleNamespace(returncode=0))
    assert rt == "docker"


def test_container_runtime_falls_back_to_podman_when_docker_is_absent(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_only("podman"))
    rt = container_runtime(run=lambda *a, **k: SimpleNamespace(returncode=0))
    assert rt == "podman"


def test_container_runtime_is_none_when_neither_is_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_only())
    assert container_runtime(run=lambda *a, **k: SimpleNamespace(returncode=0)) is None


def test_container_runtime_skips_a_binary_whose_info_fails(monkeypatch):
    # `docker` on PATH but its daemon is down (info rc != 0, e.g. a stopped
    # Colima VM) -> not usable -> fall through to a working podman.
    monkeypatch.setattr("shutil.which", _which_only("docker", "podman"))

    def run(cmd, **k):
        return SimpleNamespace(returncode=0 if cmd[0] == "podman" else 1)

    assert container_runtime(run=run) == "podman"


def test_container_runtime_treats_an_info_exception_as_unusable(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_only("docker"))

    def run(cmd, **k):
        raise OSError("daemon gone")

    assert container_runtime(run=run) is None


# --- probe_container_runtime: the informative form doctor renders -----------
# issue #50: on `ainode` docker was installed but `docker info` failed, and the
# check collapsed that to a bare "no runtime". The probe must instead say WHICH
# binary was found and WHY `info` failed, so doctor can show the real cause.


def test_probe_reports_the_info_failure_for_a_present_but_broken_runtime(monkeypatch):
    from nethackers.containers import probe_container_runtime

    monkeypatch.setattr("shutil.which", _which_only("docker"))

    def run(cmd, **k):
        return SimpleNamespace(
            returncode=1,
            stderr="permission denied while trying to connect to the Docker daemon socket",
            stdout="",
        )

    report = probe_container_runtime(run=run)
    assert report.runtime is None
    docker = next(c for c in report.candidates if c.exe == "docker")
    assert docker.state == "broken"
    assert "permission denied" in docker.detail
    podman = next(c for c in report.candidates if c.exe == "podman")
    assert podman.state == "absent"


def test_probe_marks_the_first_usable_runtime(monkeypatch):
    from nethackers.containers import probe_container_runtime

    monkeypatch.setattr("shutil.which", _which_only("docker", "podman"))
    report = probe_container_runtime(
        run=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout="")
    )
    assert report.runtime == "docker"
    assert [c.state for c in report.candidates] == ["usable", "usable"]
