"""Host detection for setup. Every probe is a fake passed in explicitly;
nothing here runs docker, reads the real home directory, or shells out."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nethackers.setup import host
from nethackers.setup.host import HostFacts, detect_host, setup_supported

HOME = Path("/Users/you")
ARCH_PROBE = ("/usr/bin/arch", "-x86_64", "/usr/bin/true")


def _run(answers):
    """A fake subprocess.run: ``answers`` maps an argv tuple to (returncode, stdout)."""
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kw):
        calls.append(tuple(argv))
        rc, out = answers.get(tuple(argv), (1, ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    run.calls = calls
    return run


def _which(*present):
    return lambda name: f"/usr/local/bin/{name}" if name in present else None


def _detect(**kw) -> HostFacts:
    args = dict(system="Darwin", machine="arm64", home=HOME, which=_which(), run=_run({}),
                exists=lambda p: False, os_release=lambda: {}, kernel_release=lambda: "24.5.0",
                environ={}, cpu_count=lambda: 10, memory_bytes=lambda: 16 * 1024**3)
    args.update(kw)
    return detect_host(**args)


def test_a_fresh_mac_with_homebrew():
    facts = _detect(which=_which("brew"), run=_run({ARCH_PROBE: (0, "")}))
    assert facts.brew and facts.installed == frozenset()
    assert facts.apple_silicon and facts.host_rosetta is True
    assert (facts.cpus, facts.memory_gb) == (10, 16)
    assert facts.docker_context is None and facts.colima_config is None


def test_a_mac_with_docker_desktop():
    facts = _detect(
        which=_which("docker"),
        exists=lambda p: p == Path("/Applications/Docker.app"),
        run=_run({("docker", "context", "show"): (0, "desktop-linux\n"),
                  ("docker", "desktop", "version"): (0, "Docker Desktop 4.92.0\n")}),
    )
    assert {"docker", "docker-desktop"} <= facts.installed
    assert facts.docker_context == "desktop-linux"
    assert facts.docker_desktop_cli is True


def test_docker_desktop_without_its_cli_plugin():
    facts = _detect(which=_which("docker"), exists=lambda p: p == Path("/Applications/Docker.app"),
                    run=_run({("docker", "context", "show"): (0, "desktop-linux\n")}))
    assert facts.docker_desktop_cli is False


def test_the_colima_profile_follows_the_docker_context():
    cfg = HOME / ".colima" / "work" / "colima.yaml"
    facts = _detect(which=_which("docker", "colima"),
                    run=_run({("docker", "context", "show"): (0, "colima-work\n")}),
                    exists=lambda p: p == cfg)
    assert facts.colima_config == cfg and facts.colima_vm is True


def test_colima_home_overrides_the_default_location():
    facts = _detect(which=_which("colima"), environ={"COLIMA_HOME": "/opt/colima"},
                    exists=lambda p: p == Path("/opt/colima"))
    assert facts.colima_config == Path("/opt/colima/default/colima.yaml")
    assert facts.colima_vm is False


# Colima's own precedence (config/files.go): $COLIMA_HOME when it exists; else
# ~/.colima when it exists; else an existing $XDG_CONFIG_HOME/colima (or
# ~/.config/colima); else ~/.colima.
@pytest.mark.parametrize("environ,present,expected", [
    ({"COLIMA_HOME": "/opt/colima"}, set(), HOME / ".colima"),          # set but not created
    ({}, {HOME / ".config" / "colima"}, HOME / ".config" / "colima"),
    ({"XDG_CONFIG_HOME": "/x/cfg"}, {Path("/x/cfg/colima")}, Path("/x/cfg/colima")),
    ({"XDG_CONFIG_HOME": "/x/cfg"}, {HOME / ".config" / "colima"}, HOME / ".colima"),
    ({}, {HOME / ".colima", HOME / ".config" / "colima"}, HOME / ".colima"),   # ~/.colima wins
    ({}, set(), HOME / ".colima"),
], ids=["colima-home-missing", "xdg-default-dir", "xdg-config-home", "xdg-elsewhere-unused",
        "dot-colima-wins", "nothing-yet"])
def test_colimas_config_directory_follows_colimas_own_precedence(environ, present, expected):
    facts = _detect(which=_which("colima"), environ=environ, exists=lambda p: p in present)
    assert facts.colima_config == expected / "default" / "colima.yaml"


def test_orbstack_is_found_by_its_cli_or_its_app():
    assert "orbstack" in _detect(which=_which("orb")).installed
    assert "orbstack" in _detect(exists=lambda p: p == Path("/Applications/OrbStack.app")).installed


def test_a_podman_machine_is_detected_on_macos():
    listing = '[{"Name": "podman-machine-default"}]'
    facts = _detect(which=_which("podman"),
                    run=_run({("podman", "machine", "list", "--format", "json"): (0, listing)}))
    assert facts.podman_machine is True
    assert _detect(which=_which("podman"),
                   run=_run({("podman", "machine", "list", "--format", "json"): (0, "[]")})
                   ).podman_machine is False


def test_rosetta_is_probed_only_on_apple_silicon():
    run = _run({})
    facts = _detect(machine="x86_64", run=run)
    assert facts.host_rosetta is None and not facts.apple_silicon
    assert ARCH_PROBE not in run.calls
    assert _detect(run=_run({})).host_rosetta is False


def test_linux_distro_families():
    ubuntu = _detect(system="Linux", machine="x86_64",
                     os_release=lambda: {"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS"})
    assert (ubuntu.distro, ubuntu.distro_name) == ("debian", "Ubuntu 24.04 LTS")
    endeavour = _detect(system="Linux", machine="x86_64",
                        os_release=lambda: {"ID": "endeavouros", "ID_LIKE": "arch"})
    assert endeavour.distro == "arch"
    rocky = _detect(system="Linux", machine="x86_64",
                    os_release=lambda: {"ID": "rocky", "ID_LIKE": "rhel centos fedora"})
    assert rocky.distro == "fedora"
    alpine = _detect(system="Linux", machine="x86_64",
                     os_release=lambda: {"ID": "alpine", "NAME": "Alpine Linux"})
    assert (alpine.distro, alpine.distro_name) == (None, "Alpine Linux")


def test_a_missing_os_release_is_not_an_error():
    def missing():
        raise OSError("no /etc/os-release")
    assert _detect(system="Linux", machine="x86_64", os_release=missing).distro is None


def test_wsl_comes_from_the_kernel_or_the_environment():
    wsl_kernel = "5.15.153.1-microsoft-standard-WSL2"
    assert _detect(system="Linux", machine="x86_64", kernel_release=lambda: wsl_kernel).wsl
    assert _detect(system="Linux", machine="x86_64", environ={"WSL_DISTRO_NAME": "Ubuntu"}).wsl
    assert not _detect(system="Linux", machine="x86_64").wsl


def test_unknown_memory_is_zero_not_a_guess():
    assert _detect(memory_bytes=lambda: None).memory_gb == 0


def test_a_probe_that_raises_counts_as_absent():
    def boom(argv, **_kw):
        raise OSError("docker vanished")
    facts = _detect(which=_which("docker"), run=boom)
    assert facts.docker_context is None and facts.docker_desktop_cli is False


def test_setup_covers_macos_and_linux_only():
    assert setup_supported("Darwin") and setup_supported("Linux")
    assert not setup_supported("Windows")
    assert "WSL2" in host.NOT_COVERED


def test_read_text_is_none_when_unreadable(tmp_path):
    assert host.read_text(tmp_path / "absent") is None
    (tmp_path / "f").write_text("hi")
    assert host.read_text(tmp_path / "f") == "hi"
