"""Linux recipes: installs and starts that need sudo are printed, picked by
distro family; only the coding agents' own installers are run."""
from __future__ import annotations

import pytest

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.setup import linux
from nethackers.setup.host import HostFacts
from nethackers.setup.support import NotCovered

UP = RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))
NONE = RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                            RuntimeCandidate("podman", "absent", "")))


def lin(**kw) -> HostFacts:
    base: dict = dict(system="Linux", machine="x86_64")
    base.update(kw)
    return HostFacts(**base)


def broken(exe: str, error: str) -> RuntimeReport:
    other = "podman" if exe == "docker" else "docker"
    return RuntimeReport(None, (RuntimeCandidate(exe, "broken", error),
                                RuntimeCandidate(other, "absent", "")))


@pytest.mark.parametrize("facts,report,expected", [
    (lin(distro="debian"), UP, []),
    (lin(distro="debian"), NONE, ["linux.docker.install"]),
    (lin(distro="fedora"), NONE, ["linux.docker.install"]),
    (lin(distro="arch"), NONE, ["linux.docker.install-arch"]),
    (lin(), NONE, ["linux.docker.install-other"]),
    (lin(distro="debian", wsl=True), NONE, ["linux.docker.wsl"]),
    (lin(distro="debian"), broken("docker", "Cannot connect to the Docker daemon at "
                                            "unix:///var/run/docker.sock"), ["linux.docker.start"]),
    (lin(distro="debian"), broken("docker", "permission denied while trying to connect to the "
                                            "Docker daemon socket"), ["linux.docker.group"]),
    (lin(distro="debian", wsl=True), broken("docker", "Cannot connect to the Docker daemon"),
     ["linux.docker.wsl"]),
    (lin(distro="arch"), broken("podman", "cannot find UID/GID for user you"),
     ["linux.podman.fix"]),
], ids=["usable", "debian", "fedora", "arch", "other-distro", "wsl", "daemon-down",
        "no-socket-permission", "wsl-daemon-down", "podman-broken"])
def test_runtime_recipes(facts, report, expected):
    assert [r.id for r in linux.runtime_recipes(facts, report)] == expected


def test_only_the_agent_installers_are_run_everything_else_is_printed():
    for recipe in linux.RECIPES:
        if recipe.id in {"linux.claude.install", "linux.codex.install"}:
            assert recipe.who == "nethackers", recipe.id
        else:
            assert recipe.who == "you", recipe.id


def test_the_docker_script_line_also_says_to_join_the_docker_group():
    assert "get.docker.com | sudo sh" in linux.DOCKER_INSTALL.say
    assert "usermod -aG docker" in linux.DOCKER_INSTALL.say


@pytest.mark.parametrize("distro,command", [
    ("debian", "sudo apt install gh"), ("fedora", "sudo dnf install gh"),
    ("arch", "sudo pacman -S github-cli"),
])
def test_gh_install_by_family(distro, command):
    say = linux.gh_install_recipe(lin(distro=distro)).say
    # Distro packages can lag or be missing; gh's own page has the rest.
    assert command in say and "https://github.com/cli/cli/blob/trunk/docs/install_linux.md" in say


def test_other_distros_are_not_covered():
    assert isinstance(linux.gh_install_recipe(lin()).support, NotCovered)
    assert isinstance(linux.DOCKER_INSTALL_OTHER.support, NotCovered)


def test_agent_installers_match_macos():
    assert linux.agent_install_recipe(lin(), "claude").argv == (
        "sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash")
    assert linux.agent_install_recipe(lin(), "opencode2") is None


def test_emulation_is_unknown_and_never_fails():
    assert linux.emulation(lin())[0] == "unknown"
    assert linux.emulation(lin(machine="aarch64"))[2] is None
