"""macOS recipes: which runtime setup starts or installs, how a new Colima VM
is sized, and the per-runtime Rosetta check doctor shows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.setup import macos
from nethackers.setup.host import HostFacts

HOME = Path("/Users/you")
UP = RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))
DOWN = RuntimeReport(
    None,
    (
        RuntimeCandidate("docker", "broken", "Cannot connect to the Docker daemon"),
        RuntimeCandidate("podman", "absent", ""),
    ),
)
NONE = RuntimeReport(
    None,
    (
        RuntimeCandidate("docker", "absent", ""),
        RuntimeCandidate("podman", "absent", ""),
    ),
)


def mac(**kw) -> HostFacts:
    base: dict = dict(system="Darwin", machine="arm64", home=HOME, cpus=10, memory_gb=32,
                      host_rosetta=True)
    base.update(kw)
    return HostFacts(**base)


def ids(recipes) -> list[str]:
    return [r.id for r in recipes]


DESKTOP = dict(installed=frozenset({"docker", "docker-desktop"}), docker_context="desktop-linux")


@pytest.mark.parametrize("facts,report,expected", [
    (mac(), UP, []),
    (mac(brew=True), NONE, ["macos.colima.install", "macos.colima.start-new"]),
    (mac(), NONE, ["macos.runtime.no-homebrew"]),
    (mac(docker_desktop_cli=True, **DESKTOP), DOWN, ["macos.docker-desktop.start"]),
    (mac(**DESKTOP), DOWN, ["macos.docker-desktop.open"]),
    (mac(installed=frozenset({"docker", "colima"}), docker_context="colima", colima_vm=True),
     DOWN, ["macos.colima.start"]),
    (mac(installed=frozenset({"docker", "colima", "docker-desktop"}), docker_context="colima"),
     DOWN, ["macos.colima.start-new"]),
    (mac(installed=frozenset({"docker", "orbstack"}), docker_context="orbstack"),
     DOWN, ["macos.orbstack.start"]),
    (mac(installed=frozenset({"podman"}), podman_machine=True), NONE, ["macos.podman.start"]),
    (mac(installed=frozenset({"podman"})), NONE, ["macos.podman.init"]),
    (mac(installed=frozenset({"docker", "docker-desktop", "colima"}), docker_context="default",
         docker_desktop_cli=True), DOWN, ["macos.docker-desktop.start"]),
    # A context naming a runtime that isn't installed any more falls back to what is.
    (mac(installed=frozenset({"docker", "docker-desktop"}), docker_context="colima",
         docker_desktop_cli=True), DOWN, ["macos.docker-desktop.start"]),
], ids=["usable", "fresh-with-homebrew", "fresh-without-homebrew", "desktop-stopped",
        "desktop-without-its-cli", "colima-stopped", "colima-context-no-vm-yet",
        "orbstack-stopped", "podman-machine-stopped", "podman-without-a-machine",
        "default-context-falls-back-to-desktop", "stale-colima-context"])
def test_runtime_recipes(facts, report, expected):
    assert ids(macos.runtime_recipes(facts, report)) == expected


def test_a_new_colima_vm_uses_rosetta_and_todays_size_on_a_big_mac():
    recipe = macos.colima_start(mac(cpus=10, memory_gb=32))
    assert recipe.argv == ("colima", "start", "--vm-type", "vz", "--vz-rosetta",
                           "--cpu", "6", "--memory", "12")
    assert recipe.id == "macos.colima.start-new" and recipe.does == "start Colima with Rosetta"


def test_a_small_mac_gets_at_most_its_cpu_count_and_half_its_ram():
    assert macos.colima_start(mac(cpus=4, memory_gb=8)).argv[-4:] == ("--cpu", "4", "--memory", "4")


def test_an_unknown_size_leaves_colimas_own_defaults():
    argv = macos.colima_start(mac(cpus=0, memory_gb=0)).argv
    assert "--cpu" not in argv and "--memory" not in argv


def test_an_intel_mac_gets_no_rosetta_flags():
    recipe = macos.colima_start(mac(machine="x86_64"))
    assert "--vz-rosetta" not in recipe.argv and recipe.does == "start Colima"


def test_an_existing_colima_vm_starts_with_no_flags():
    assert macos.colima_start(mac(colima_vm=True)).argv == ("colima", "start")


def test_gh_is_installed_with_homebrew_when_there_is_one():
    assert macos.gh_install_recipe(mac(brew=True)).argv == ("brew", "install", "gh")
    assert macos.gh_install_recipe(mac()).who == "you"


def test_agent_installers_are_the_vendors_own_scripts():
    assert macos.agent_install_recipe(mac(), "claude").argv == (
        "sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash")
    assert macos.agent_install_recipe(mac(), "codex").argv == (
        "sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh")
    assert macos.agent_install_recipe(mac(), "opencode2") is None


# --- emulation: doctor's Rosetta check --------------------------------------

SETTINGS = HOME / macos.DOCKER_DESKTOP_SETTINGS


def _files(mapping):
    return lambda path: mapping.get(path)


def _desktop(settings) -> tuple:
    text = settings if isinstance(settings, str) or settings is None else json.dumps(settings)
    return macos.emulation(mac(**DESKTOP), read_text=_files({SETTINGS: text}))


def test_docker_desktop_with_rosetta_on():
    status, _detail, recipe = _desktop({"UseVirtualizationFramework": True,
                                        "UseVirtualizationFrameworkRosetta": True})
    assert status == "ok" and recipe is None


def test_docker_desktop_with_rosetta_off_warns_with_the_measured_cost():
    status, detail, recipe = _desktop({"UseVirtualizationFramework": True,
                                       "UseVirtualizationFrameworkRosetta": False})
    assert status == "warn" and "823" in detail and "224" in detail
    assert recipe is macos.ROSETTA_DOCKER_DESKTOP


def test_docker_desktop_without_the_virtualization_framework_warns():
    status, _detail, _recipe = _desktop({"UseVirtualizationFramework": False,
                                         "UseVirtualizationFrameworkRosetta": True})
    assert status == "warn"


def test_missing_docker_desktop_keys_mean_unknown_not_off():
    status, _detail, recipe = _desktop({})
    assert status == "unknown" and recipe is None


@pytest.mark.parametrize("text", [None, "not json", "[1, 2]", "null"])
def test_unreadable_docker_desktop_settings_are_unknown(text):
    status, _detail, recipe = _desktop(text)
    assert status == "unknown" and recipe is None


def test_colima_rosetta_comes_from_its_config():
    cfg = HOME / ".colima" / "default" / "colima.yaml"
    facts = mac(installed=frozenset({"docker", "colima"}), docker_context="colima",
                colima_config=cfg, colima_vm=True)
    assert macos.emulation(facts, read_text=_files({cfg: "vmType: vz\nrosetta: true\n"}))[0] == "ok"
    status, _detail, recipe = macos.emulation(facts, read_text=_files({cfg: "rosetta: false\n"}))
    assert status == "warn" and recipe is macos.ROSETTA_COLIMA


def test_orbstack_always_has_rosetta():
    facts = mac(installed=frozenset({"docker", "orbstack"}), docker_context="orbstack")
    assert macos.emulation(facts)[0] == "ok"


def test_podman_warns_that_amd64_is_slow():
    status, _detail, recipe = macos.emulation(mac(installed=frozenset({"podman"})))
    assert status == "warn" and recipe is macos.PODMAN_SLOW


def test_missing_rosetta_on_the_mac_is_reported_first():
    status, _detail, recipe = macos.emulation(mac(host_rosetta=False, **DESKTOP))
    assert status == "warn" and recipe is macos.ROSETTA_INSTALL


def test_intel_macs_and_other_systems_are_unknown():
    assert macos.emulation(mac(machine="x86_64"))[0] == "unknown"
    assert macos.emulation(HostFacts("Linux", "x86_64"))[0] == "unknown"


@pytest.mark.parametrize("facts", [
    mac(**DESKTOP), mac(installed=frozenset({"podman"})), mac(host_rosetta=False),
    mac(machine="x86_64"), mac(), mac(installed=frozenset({"docker", "colima"}),
                                      docker_context="colima", colima_config=HOME / "x.yaml"),
])
def test_emulation_is_advice_and_never_fails(facts):
    assert macos.emulation(facts, read_text=lambda p: None)[0] in {"ok", "warn", "unknown"}
