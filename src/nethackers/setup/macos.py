"""macOS: what ``nethackers setup`` does on a Mac, as recipes (``support``).

A fresh Mac with Homebrew gets Colima, started with Rosetta: it's headless,
free, needs no sudo, and its flags alone decide Rosetta. Rosetta 2 itself
must be there first, so without it the new VM waits for the next run. An
installed Docker Desktop, OrbStack or Podman machine is kept and started.
Anything that needs sudo or a GUI click is printed, never run. Which runtime
``docker`` talks to comes from ``docker context show`` -- Colima, Docker
Desktop and OrbStack each set their own -- not from which apps happen to be
installed.

``emulation`` is doctor's Rosetta check, per runtime: Docker Desktop's
settings file, Colima's config, OrbStack (always Rosetta), Podman (Rosetta
off by default on Apple Silicon since Podman 5.6). It never guesses: it says
"unknown" whenever it can't tell.

Leaf module: imports only ``setup.host``, ``setup.support`` and
``containers``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from nethackers.containers import RuntimeReport
from nethackers.setup.host import HostFacts, read_text as _read_text
from nethackers.setup.support import Recipe, Untested

# Measured on one machine, one 15-episode identity batch, same games both
# sides (spec 2026-09-14 section 8).
COST = "same batch: 823s under QEMU vs 224s with Rosetta"

# Relative to the home directory.
DOCKER_DESKTOP_SETTINGS = Path("Library/Group Containers/group.com.docker/settings-store.json")

_COLIMA = "https://github.com/abiosoft/colima#readme"
_COLIMA_START = "https://github.com/abiosoft/colima/blob/main/cmd/start.go"
_DD_INSTALL = "https://docs.docker.com/desktop/setup/install/mac-install/"
_DD_CLI = "https://docs.docker.com/reference/cli/docker/desktop/start/"
_DD_SETTINGS = "https://docs.docker.com/desktop/settings-and-maintenance/settings/"
_ORB = "https://docs.orbstack.dev/headless"
_PODMAN_START = "https://docs.podman.io/en/latest/markdown/podman-machine-start.1.html"
_PODMAN_INIT = "https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html"
_PODMAN_NOTES = "https://github.com/containers/podman/blob/main/RELEASE_NOTES.md"
_BREW = "https://brew.sh"
_GH = "https://github.com/cli/cli#installation"
_CLAUDE = "https://code.claude.com/docs/en/setup"
_CODEX = "https://github.com/openai/codex"
_ROSETTA = "https://support.apple.com/en-us/102527"

COLIMA_INSTALL = Recipe(
    id="macos.colima.install", does="install Colima + Docker", who="nethackers",
    argv=("brew", "install", "colima", "docker"), support=Untested(_COLIMA))
COLIMA_START_NEW = Recipe(
    id="macos.colima.start-new", does="start Colima with Rosetta", who="nethackers",
    argv=("colima", "start", "--vm-type", "vz", "--vz-rosetta", "--cpu", "6", "--memory", "12"),
    support=Untested(_COLIMA_START))
COLIMA_START = Recipe(
    id="macos.colima.start", does="start Colima", who="nethackers",
    argv=("colima", "start"), support=Untested(_COLIMA))
DOCKER_DESKTOP_START = Recipe(
    id="macos.docker-desktop.start", does="start Docker Desktop", who="nethackers",
    argv=("docker", "desktop", "start"), support=Untested(_DD_CLI))
DOCKER_DESKTOP_OPEN = Recipe(
    id="macos.docker-desktop.open", does="start Docker Desktop", who="you",
    say="open Docker Desktop and wait until it says the engine is running",
    support=Untested(_DD_INSTALL))
ORBSTACK_START = Recipe(
    id="macos.orbstack.start", does="start OrbStack", who="nethackers",
    argv=("orb", "start"), support=Untested(_ORB))
PODMAN_START = Recipe(
    id="macos.podman.start", does="start the Podman machine", who="nethackers",
    argv=("podman", "machine", "start"), support=Untested(_PODMAN_START))
PODMAN_INIT = Recipe(
    id="macos.podman.init", does="create a Podman machine", who="you",
    say="create and start a Podman machine: `podman machine init && podman machine start`",
    support=Untested(_PODMAN_INIT))
NO_HOMEBREW = Recipe(
    id="macos.runtime.no-homebrew", does="install a container runtime", who="you",
    say=("install Homebrew (https://brew.sh; its installer uses sudo), or install Docker "
         "Desktop (https://docs.docker.com/desktop/setup/install/mac-install/) or OrbStack "
         "(https://orbstack.dev) yourself"),
    support=Untested(_BREW))
GH_INSTALL = Recipe(
    id="macos.gh.install", does="install the GitHub CLI", who="nethackers",
    argv=("brew", "install", "gh"), support=Untested(_GH))
GH_NO_HOMEBREW = Recipe(
    id="macos.gh.no-homebrew", does="install the GitHub CLI", who="you",
    say="install the GitHub CLI from https://cli.github.com", support=Untested(_GH))
CLAUDE_INSTALL = Recipe(
    id="macos.claude.install", does="install Claude Code", who="nethackers",
    argv=("sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"),
    support=Untested(_CLAUDE))
CODEX_INSTALL = Recipe(
    id="macos.codex.install", does="install Codex", who="nethackers",
    argv=("sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh"),
    support=Untested(_CODEX))
ROSETTA_INSTALL = Recipe(
    id="macos.rosetta.install", does="install Rosetta", who="you",
    say="install Rosetta: `softwareupdate --install-rosetta --agree-to-license`",
    support=Untested(_ROSETTA))
ROSETTA_DOCKER_DESKTOP = Recipe(
    id="macos.docker-desktop.rosetta", does="turn on Rosetta in Docker Desktop", who="you",
    say=('turn on Rosetta: Docker Desktop → Settings → General → "Apple Virtualization '
         'framework" and "Use Rosetta for x86_64/amd64 emulation" (Docker Desktop restarts)'),
    support=Untested(_DD_SETTINGS))
ROSETTA_COLIMA = Recipe(
    id="macos.colima.rosetta", does="recreate Colima with Rosetta", who="you",
    say=("recreate Colima with Rosetta: `colima delete` (this deletes its images and "
         "containers), then run `nethackers setup` again"),
    support=Untested(_COLIMA_START))
PODMAN_SLOW = Recipe(
    id="macos.podman.no-rosetta", does="use a runtime with Rosetta", who="you",
    say=("Podman runs amd64 under QEMU on Apple Silicon; for faster evaluation use Colima "
         "(`brew install colima docker`, then `nethackers setup`) or OrbStack"),
    support=Untested(_PODMAN_NOTES))

RECIPES: tuple[Recipe, ...] = (
    COLIMA_INSTALL, COLIMA_START_NEW, COLIMA_START, DOCKER_DESKTOP_START, DOCKER_DESKTOP_OPEN,
    ORBSTACK_START, PODMAN_START, PODMAN_INIT, NO_HOMEBREW, GH_INSTALL, GH_NO_HOMEBREW,
    CLAUDE_INSTALL, CODEX_INSTALL, ROSETTA_INSTALL, ROSETTA_DOCKER_DESKTOP, ROSETTA_COLIMA,
    PODMAN_SLOW,
)

# Today's size (the old doctor hint): 6 CPUs, 12 GB -- capped to the machine:
# at most its CPU count, and at most half its RAM so macOS keeps room.
_COLIMA_CPUS = 6
_COLIMA_MEMORY_GB = 12


def active_provider(facts: HostFacts) -> str | None:
    """Which installed runtime this Mac's ``docker`` (or Podman) is set up to
    use: "colima", "docker-desktop", "orbstack", "podman", or ``None`` when
    none is installed. The docker context decides when it names an installed
    runtime; otherwise the installed apps are the fallback."""
    context = facts.docker_context or ""
    if (context == "colima" or context.startswith("colima-")) and "colima" in facts.installed:
        return "colima"
    if context == "desktop-linux" and "docker-desktop" in facts.installed:
        return "docker-desktop"
    if context == "orbstack" and "orbstack" in facts.installed:
        return "orbstack"
    for name in ("docker-desktop", "orbstack", "colima", "podman"):
        if name in facts.installed:
            return name
    return None


def colima_start(facts: HostFacts) -> Recipe:
    """Start the existing Colima VM as it is, or create one sized to this Mac
    (with Rosetta on Apple Silicon)."""
    if facts.colima_vm:
        return COLIMA_START
    argv = ["colima", "start"]
    if facts.apple_silicon:
        argv += ["--vm-type", "vz", "--vz-rosetta"]
    if facts.cpus and facts.memory_gb:
        memory = max(2, min(_COLIMA_MEMORY_GB, facts.memory_gb // 2))
        argv += ["--cpu", str(min(_COLIMA_CPUS, facts.cpus)), "--memory", str(memory)]
    does = "start Colima with Rosetta" if facts.apple_silicon else "start Colima"
    return replace(COLIMA_START_NEW, argv=tuple(argv), does=does)


def _new_colima_vm(facts: HostFacts) -> tuple[Recipe, ...]:
    """Create and start a Colima VM -- unless it would start with Rosetta on a
    Mac that doesn't have Rosetta 2 yet (a new Mac gets it only when something
    asks), which pops Apple's dialog or fails mid-run. Then the person installs
    Rosetta first, and the start (and the pull after it) waits for the next
    run."""
    if facts.apple_silicon and facts.host_rosetta is False:
        return (ROSETTA_INSTALL,)
    return (colima_start(facts),)


def runtime_recipes(facts: HostFacts, report: RuntimeReport) -> tuple[Recipe, ...]:
    """What gets a container runtime answering on this Mac; ``()`` when one
    already does. An existing VM or another runtime starts without Rosetta;
    the emulation advice covers those after the run."""
    if report.runtime is not None:
        return ()
    provider = active_provider(facts)
    if provider == "colima":
        return (COLIMA_START,) if facts.colima_vm else _new_colima_vm(facts)
    if provider == "docker-desktop":
        return (DOCKER_DESKTOP_START,) if facts.docker_desktop_cli else (DOCKER_DESKTOP_OPEN,)
    if provider == "orbstack":
        return (ORBSTACK_START,)
    if provider == "podman":
        return (PODMAN_START,) if facts.podman_machine else (PODMAN_INIT,)
    if facts.brew:
        return (COLIMA_INSTALL, *_new_colima_vm(facts))
    return (NO_HOMEBREW,)


def gh_install_recipe(facts: HostFacts) -> Recipe:
    return GH_INSTALL if facts.brew else GH_NO_HOMEBREW


def agent_install_recipe(facts: HostFacts, operator: str) -> Recipe | None:
    """The vendor's own installer for a coding agent; ``None`` for OpenCode,
    whose CLI ships in the sandbox."""
    return {"claude": CLAUDE_INSTALL, "codex": CODEX_INSTALL}.get(operator)


_COLIMA_ROSETTA_ON = re.compile(r"^\s*rosetta:\s*true\b", re.MULTILINE)
_UNREADABLE_DD = "could not read Docker Desktop's settings"


def emulation(
    facts: HostFacts, *, read_text: Callable[[Path], str | None] = _read_text,
) -> tuple[str, str, Recipe | None]:
    """``(status, detail, recipe)`` for amd64 emulation on this Mac. ``status``
    is "ok", "warn" or "unknown" -- never "fail": this is advice, not a gate.
    ``recipe`` is what turns Rosetta on when it's off, else ``None``."""
    if not facts.apple_silicon:
        return "unknown", "not an Apple Silicon Mac; amd64 emulation does not apply", None
    if facts.host_rosetta is False:
        return ("warn", f"Rosetta isn't installed on this Mac, so amd64 runs under QEMU ({COST})",
                ROSETTA_INSTALL)
    provider = active_provider(facts)
    if provider == "docker-desktop":
        return _docker_desktop(read_text(facts.home / DOCKER_DESKTOP_SETTINGS))
    if provider == "colima":
        return _colima(read_text(facts.colima_config) if facts.colima_config else None)
    if provider == "orbstack":
        return "ok", "OrbStack runs amd64 with Rosetta", None
    if provider == "podman":
        return "warn", f"Podman runs amd64 under QEMU on Apple Silicon ({COST})", PODMAN_SLOW
    return "unknown", "no container runtime yet, so nothing to check", None


def _docker_desktop(text: str | None) -> tuple[str, str, Recipe | None]:
    if text is None:
        return "unknown", _UNREADABLE_DD, None
    try:
        settings = json.loads(text)
    except ValueError:
        return "unknown", _UNREADABLE_DD, None
    if not isinstance(settings, dict):
        return "unknown", _UNREADABLE_DD, None
    vz = settings.get("UseVirtualizationFramework")
    rosetta = settings.get("UseVirtualizationFrameworkRosetta")
    if vz is None or rosetta is None:
        # A missing key is not "off": the file may only hold changed settings.
        return "unknown", "Docker Desktop's settings don't say whether Rosetta is on", None
    if vz and rosetta:
        return "ok", "Rosetta is accelerating amd64 emulation", None
    return ("warn", f"amd64 evaluation is running under QEMU, not Rosetta ({COST})",
            ROSETTA_DOCKER_DESKTOP)


def _colima(text: str | None) -> tuple[str, str, Recipe | None]:
    if text is None:
        return "unknown", "could not read Colima's config", None
    if _COLIMA_ROSETTA_ON.search(text):
        return "ok", "Colima runs amd64 with Rosetta", None
    return "warn", f"Colima runs amd64 under QEMU, not Rosetta ({COST})", ROSETTA_COLIMA
