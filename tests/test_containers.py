import re
from types import SimpleNamespace

from nethackers.containers import (
    NETHACKERS_LABEL,
    container_name,
    container_runtime,
    label_args,
    nonroot_userns_args,
    runtime_capacity,
)


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


# --- nonroot_userns_args: rootless podman's uid mapping (issue #54) ---------
# Rootless podman maps the host user to container uid 0, so a bind-mounted
# host dir stats as root-owned INSIDE the container. The mutator cage both
# bind-mounts the worktree AND drops to the non-root `agent` user, so under
# rootless podman that drop lands on a /workspace it cannot write (EACCES).
# `--userns=keep-id` maps the host uid to itself instead; `--user 0` keeps the
# entrypoint at container-root (keep-id otherwise overrides the image's user
# to the host uid, with an EMPTY capability set, which would break the
# entrypoint's usermod/chown/gosu).


def _info_rootless(value: str, *, rc: int = 0):
    """A `run` stub answering `<exe> info --format {{.Host.Security.Rootless}}`."""
    return lambda *a, **k: SimpleNamespace(returncode=rc, stdout=value, stderr="")


def test_nonroot_userns_args_are_empty_for_docker():
    # docker has no such `info` field -- the template errors, rc != 0.
    assert nonroot_userns_args("docker", run=_info_rootless("", rc=1)) == []


def test_nonroot_userns_args_keep_id_for_rootless_podman():
    assert nonroot_userns_args("podman", run=_info_rootless("true\n")) == [
        "--userns=keep-id", "--user", "0",
    ]


def test_nonroot_userns_args_are_empty_for_rootful_podman():
    # rootful podman sees real host ownership on bind mounts, exactly like
    # docker -- and keep-id is rejected outright on podman 4.1-4.4.
    assert nonroot_userns_args("podman", run=_info_rootless("false\n")) == []


def test_nonroot_userns_args_are_empty_when_the_probe_fails():
    def run(*a, **k):
        raise OSError("boom")

    # never let a probe failure break a run that works today: fall back to the
    # no-extra-args behavior rather than guessing.
    assert nonroot_userns_args("podman", run=run) == []


def test_nonroot_userns_args_probe_asks_for_the_rootless_field():
    seen: list = []

    def run(cmd, **k):
        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="true", stderr="")

    nonroot_userns_args("podman", run=run)
    assert seen == [["podman", "info", "--format", "{{.Host.Security.Rootless}}"]]


# --- runtime_capacity: what the runtime can give its containers ------------
# The arena box is sized from this (eval/runner.py's _size_box), so it must
# answer on docker AND podman, and degrade to None -- today's fixed default --
# on any failure rather than guess.


def _info_by_template(answers):
    """A `run` stub answering `<exe> info --format <template>` per template:
    a string is stdout at rc 0, None is the template error (rc 1)."""
    seen = []

    def run(cmd, **kw):
        fmt = cmd[cmd.index("--format") + 1]
        seen.append(fmt)
        out = answers.get(fmt)
        return SimpleNamespace(returncode=1 if out is None else 0, stdout=out or "", stderr="")

    run.seen = seen
    return run


def test_runtime_capacity_reads_dockers_fields():
    run = _info_by_template({"{{.NCPU}} {{.MemTotal}}": "10 8341884928\n"})
    assert runtime_capacity("docker", run=run) == (10, 8341884928)


def test_runtime_capacity_falls_through_to_podmans_host_block():
    # podman errors on docker's top-level NCPU; its numbers live under Host.
    run = _info_by_template({"{{.Host.CPUs}} {{.Host.MemTotal}}": "96 404620763136\n"})
    assert runtime_capacity("podman", run=run) == (96, 404620763136)
    assert run.seen == ["{{.NCPU}} {{.MemTotal}}", "{{.Host.CPUs}} {{.Host.MemTotal}}"]


def test_runtime_capacity_is_none_when_nothing_answers():
    assert runtime_capacity("docker", run=_info_by_template({})) is None
    garbled = _info_by_template({"{{.NCPU}} {{.MemTotal}}": "<no value>",
                                 "{{.Host.CPUs}} {{.Host.MemTotal}}": "0 0"})
    assert runtime_capacity("docker", run=garbled) is None


def test_runtime_capacity_is_none_when_the_probe_fails():
    def run(*a, **k):
        raise OSError("no such binary")

    assert runtime_capacity("docker", run=run) is None
