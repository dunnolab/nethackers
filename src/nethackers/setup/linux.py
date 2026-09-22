"""Linux: what ``nethackers setup`` does on Linux, as recipes (``support``).

Installing or starting a container runtime, and installing ``gh``, all need
sudo on Linux -- so those are printed for the person, picked by distro family
from /etc/os-release, and setup re-checks on its next run. The coding agents'
own installers need no sudo, so setup runs them. The printed runtime is Docker
Engine; an installed Podman (rootless or not) is detected and used, but setup
doesn't install it (the rootless path hasn't been run on a real host yet).
WSL2 counts as Linux, with a note about Docker Desktop's WSL integration.

Leaf module: imports only ``setup.host``, ``setup.support`` and
``containers``.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nethackers.containers import RuntimeReport
from nethackers.setup.host import HostFacts
from nethackers.setup.support import NotCovered, Recipe, Untested

_DOCKER_SCRIPT = ("https://docs.docker.com/engine/install/ubuntu/"
                  "#install-using-the-convenience-script")
_DOCKER_POSTINSTALL = "https://docs.docker.com/engine/install/linux-postinstall/"
_ARCH_DOCKER = "https://wiki.archlinux.org/title/Docker"
_DOCKER_INSTALL = "https://docs.docker.com/engine/install/"
_WSL = "https://docs.docker.com/desktop/features/wsl/"
_PODMAN_ROOTLESS = ("https://github.com/containers/podman/blob/main/docs/tutorials/"
                    "rootless_tutorial.md")
_GH_LINUX = "https://github.com/cli/cli/blob/trunk/docs/install_linux.md"
_CLAUDE = "https://code.claude.com/docs/en/setup"
_CODEX = "https://github.com/openai/codex"

_GROUP = "then `sudo usermod -aG docker $USER` and log out and back in"

DOCKER_INSTALL = Recipe(
    id="linux.docker.install", does="install Docker", who="you",
    say=f"install Docker: `curl -fsSL https://get.docker.com | sudo sh`, {_GROUP}",
    support=Untested(_DOCKER_SCRIPT))
DOCKER_INSTALL_ARCH = Recipe(
    id="linux.docker.install-arch", does="install Docker", who="you",
    say=("install Docker: `sudo pacman -S docker && sudo systemctl enable --now docker`, "
         f"{_GROUP}"),
    support=Untested(_ARCH_DOCKER))
DOCKER_INSTALL_OTHER = Recipe(
    id="linux.docker.install-other", does="install Docker", who="you",
    say="install Docker for your distribution: https://docs.docker.com/engine/install/",
    support=NotCovered(_DOCKER_INSTALL))
DOCKER_WSL = Recipe(
    id="linux.docker.wsl", does="get Docker working in WSL", who="you",
    say=("install Docker Desktop for Windows and turn on its WSL integration for this "
         "distro (https://docs.docker.com/desktop/features/wsl/), or install Docker Engine "
         "inside WSL: `curl -fsSL https://get.docker.com | sudo sh`"),
    support=Untested(_WSL))
DOCKER_START = Recipe(
    id="linux.docker.start", does="start Docker", who="you",
    say="start Docker: `sudo systemctl enable --now docker`",
    support=Untested(_DOCKER_POSTINSTALL))
DOCKER_GROUP = Recipe(
    id="linux.docker.group", does="let your user reach Docker", who="you",
    say=("let your user reach Docker: `sudo usermod -aG docker $USER`, then log out and "
         "back in (or run `newgrp docker` in this shell)"),
    support=Untested(_DOCKER_POSTINSTALL))
PODMAN_FIX = Recipe(
    id="linux.podman.fix", does="fix Podman", who="you",
    say=("fix Podman (its error is shown above); rootless Podman needs subuid/subgid "
         f"ranges: {_PODMAN_ROOTLESS}"),
    support=Untested(_PODMAN_ROOTLESS))
# A distro's package can lag behind or be missing (older Debian and Ubuntu);
# gh's own page covers the rest, so every line carries it.
GH_APT = Recipe(
    id="linux.gh.apt", does="install the GitHub CLI", who="you",
    say=f"install the GitHub CLI: `sudo apt install gh` (or see {_GH_LINUX})",
    support=Untested(_GH_LINUX))
GH_DNF = Recipe(
    id="linux.gh.dnf", does="install the GitHub CLI", who="you",
    say=f"install the GitHub CLI: `sudo dnf install gh` (or see {_GH_LINUX})",
    support=Untested(_GH_LINUX))
GH_PACMAN = Recipe(
    id="linux.gh.pacman", does="install the GitHub CLI", who="you",
    say=f"install the GitHub CLI: `sudo pacman -S github-cli` (or see {_GH_LINUX})",
    support=Untested(_GH_LINUX))
GH_OTHER = Recipe(
    id="linux.gh.other", does="install the GitHub CLI", who="you",
    say=f"install the GitHub CLI: {_GH_LINUX}", support=NotCovered(_GH_LINUX))
CLAUDE_INSTALL = Recipe(
    id="linux.claude.install", does="install Claude Code", who="nethackers",
    argv=("sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"),
    support=Untested(_CLAUDE))
CODEX_INSTALL = Recipe(
    id="linux.codex.install", does="install Codex", who="nethackers",
    argv=("sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh"),
    support=Untested(_CODEX))

RECIPES: tuple[Recipe, ...] = (
    DOCKER_INSTALL, DOCKER_INSTALL_ARCH, DOCKER_INSTALL_OTHER, DOCKER_WSL, DOCKER_START,
    DOCKER_GROUP, PODMAN_FIX, GH_APT, GH_DNF, GH_PACMAN, GH_OTHER, CLAUDE_INSTALL,
    CODEX_INSTALL,
)

_INSTALL_BY_FAMILY = {"debian": DOCKER_INSTALL, "fedora": DOCKER_INSTALL,
                      "arch": DOCKER_INSTALL_ARCH}
_GH_BY_FAMILY = {"debian": GH_APT, "fedora": GH_DNF, "arch": GH_PACMAN}


def runtime_recipes(facts: HostFacts, report: RuntimeReport) -> tuple[Recipe, ...]:
    """What gets a container runtime answering here; ``()`` when one already
    does. All of it is printed: every step needs sudo on Linux."""
    if report.runtime is not None:
        return ()
    broken = {c.exe: c.detail.lower() for c in report.candidates if c.state == "broken"}
    if "docker" in broken:
        if "permission denied" in broken["docker"]:
            return (DOCKER_GROUP,)
        return (DOCKER_WSL,) if facts.wsl else (DOCKER_START,)
    if "podman" in broken:
        return (PODMAN_FIX,)
    if facts.wsl:
        return (DOCKER_WSL,)
    return (_INSTALL_BY_FAMILY.get(facts.distro or "", DOCKER_INSTALL_OTHER),)


def gh_install_recipe(facts: HostFacts) -> Recipe:
    return _GH_BY_FAMILY.get(facts.distro or "", GH_OTHER)


def agent_install_recipe(facts: HostFacts, operator: str) -> Recipe | None:
    """The vendor's own installer for a coding agent; ``None`` for OpenCode,
    whose CLI ships in the sandbox."""
    return {"claude": CLAUDE_INSTALL, "codex": CODEX_INSTALL}.get(operator)


def emulation(
    facts: HostFacts, *, read_text: Callable[[Path], str | None] | None = None,
) -> tuple[str, str, Recipe | None]:
    """Always "unknown": x86_64 Linux runs amd64 natively, and nethackers
    doesn't check emulation on other CPUs. Advice only, never a gate."""
    if facts.machine in {"x86_64", "amd64"}:
        return "unknown", "amd64 runs natively here", None
    return "unknown", "amd64 emulation isn't checked on Linux", None
