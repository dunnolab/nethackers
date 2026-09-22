"""What this machine is, as far as setup cares.

``detect_host`` gathers it once: the OS, Homebrew or the Linux distro family,
which container runtimes are installed, which one ``docker`` is pointed at
(``docker context show``), and on Apple Silicon whether Rosetta is there.
Every probe is a parameter, passed explicitly by tests -- never patched -- so
no test shells out. ``platform_for`` picks the OS file (``setup.macos`` or
``setup.linux``), or ``None`` where setup doesn't cover the OS (native
Windows).

Leaf module: stdlib only. The OS files are imported inside ``platform_for``,
so importing this module never imports them.
"""
from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

NOT_COVERED = ("native Windows isn't covered; run nethackers inside WSL2 "
               "(https://learn.microsoft.com/windows/wsl/install)")

# The same budget containers.probe_container_runtime gives `<exe> info`.
_PROBE_TIMEOUT = 10

_APPLE_SILICON = frozenset({"arm64", "aarch64"})


@dataclass(frozen=True)
class HostFacts:
    system: str                               # platform.system()
    machine: str                              # platform.machine()
    home: Path = Path("/nonexistent")
    brew: bool = False                        # Homebrew on PATH
    distro: str | None = None                 # "debian" | "fedora" | "arch" | None
    distro_name: str | None = None            # PRETTY_NAME, for messages
    wsl: bool = False
    installed: frozenset[str] = frozenset()   # docker, podman, colima, orbstack, docker-desktop
    docker_context: str | None = None         # `docker context show`
    docker_desktop_cli: bool = False          # the `docker desktop` subcommand works
    colima_config: Path | None = None         # the active Colima profile's colima.yaml
    colima_vm: bool = False                   # ...and that profile has been created
    podman_machine: bool = False              # a Podman machine exists (macOS)
    host_rosetta: bool | None = None          # Apple Silicon only: Rosetta 2 installed
    cpus: int = 0                             # 0 = unknown
    memory_gb: int = 0                        # 0 = unknown

    @property
    def apple_silicon(self) -> bool:
        return self.system == "Darwin" and self.machine in _APPLE_SILICON


def setup_supported(system: str) -> bool:
    """Whether ``nethackers setup`` covers this OS at all."""
    return system in {"Darwin", "Linux"}


def platform_for(facts: HostFacts) -> ModuleType | None:
    """The OS file for this machine, or ``None`` when setup doesn't cover it."""
    if facts.system == "Darwin":
        from nethackers.setup import macos
        return macos
    if facts.system == "Linux":
        from nethackers.setup import linux
        return linux
    return None


def read_text(path: Path) -> str | None:
    """A file's text, or ``None`` when it can't be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def detect_host(
    *,
    system: str | None = None,
    machine: str | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., Any] = subprocess.run,
    exists: Callable[[Path], bool] = Path.exists,
    os_release: Callable[[], Mapping[str, str]] = _platform.freedesktop_os_release,
    kernel_release: Callable[[], str] = _platform.release,
    environ: Mapping[str, str] = os.environ,
    cpu_count: Callable[[], int | None] = os.cpu_count,
    memory_bytes: Callable[[], int | None] | None = None,
) -> HostFacts:
    """Probe this machine once. Each probe is cheap and local (``which``, a
    file check, ``docker context show``); a probe that fails or raises counts
    as absent, never as an error."""
    system = system or _platform.system()
    machine = machine or _platform.machine()
    home = home or Path.home()
    installed = {name for name in ("docker", "podman", "colima") if which(name)}
    if which("orb") or exists(Path("/Applications/OrbStack.app")):
        installed.add("orbstack")
    if system == "Darwin" and (exists(Path("/Applications/Docker.app"))
                               or exists(home / "Applications" / "Docker.app")):
        installed.add("docker-desktop")
    context = _first_line(run, ["docker", "context", "show"]) if "docker" in installed else None
    colima_config = None
    if "colima" in installed:
        colima_home = _colima_home(home, environ, exists)
        colima_config = colima_home / _colima_profile(context) / "colima.yaml"
    distro, distro_name = _distro(os_release) if system == "Linux" else (None, None)
    mem = (memory_bytes or _memory_bytes)()
    return HostFacts(
        system=system,
        machine=machine,
        home=home,
        brew=which("brew") is not None,
        distro=distro,
        distro_name=distro_name,
        wsl=system == "Linux" and ("microsoft" in kernel_release().lower()
                                   or bool(environ.get("WSL_DISTRO_NAME"))),
        installed=frozenset(installed),
        docker_context=context,
        docker_desktop_cli=("docker-desktop" in installed and "docker" in installed
                            and _ok(run, ["docker", "desktop", "version"])),
        colima_config=colima_config,
        colima_vm=colima_config is not None and exists(colima_config),
        podman_machine=(system == "Darwin" and "podman" in installed and _podman_machine(run)),
        host_rosetta=(_ok(run, ["/usr/bin/arch", "-x86_64", "/usr/bin/true"])
                      if system == "Darwin" and machine in _APPLE_SILICON else None),
        cpus=cpu_count() or 0,
        memory_gb=(mem // 1024**3) if mem else 0,
    )


def _probe(run: Callable[..., Any], argv: list[str]) -> Any | None:
    """Run a quick local probe; the finished process, or None if it couldn't run."""
    try:
        return run(argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None


def _ok(run: Callable[..., Any], argv: list[str]) -> bool:
    proc = _probe(run, argv)
    return proc is not None and getattr(proc, "returncode", 1) == 0


def _first_line(run: Callable[..., Any], argv: list[str]) -> str | None:
    proc = _probe(run, argv)
    if proc is None or getattr(proc, "returncode", 1) != 0:
        return None
    lines = (getattr(proc, "stdout", "") or "").strip().splitlines()
    return lines[0].strip() if lines else None


def _podman_machine(run: Callable[..., Any]) -> bool:
    proc = _probe(run, ["podman", "machine", "list", "--format", "json"])
    if proc is None:
        return False
    try:
        machines = json.loads(proc.stdout or "[]") if proc.returncode == 0 else []
    except ValueError:
        return False
    return isinstance(machines, list) and len(machines) > 0


def _colima_home(home: Path, environ: Mapping[str, str],
                 exists: Callable[[Path], bool]) -> Path:
    """Colima's config directory, by Colima's own precedence
    (https://github.com/abiosoft/colima/blob/main/config/files.go):
    $COLIMA_HOME when it exists; else ~/.colima when it exists; else an
    existing $XDG_CONFIG_HOME/colima (~/.config/colima when that is unset);
    else ~/.colima, the macOS default."""
    explicit = environ.get("COLIMA_HOME")
    if explicit and exists(Path(explicit)):
        return Path(explicit)
    dot_colima = home / ".colima"
    if exists(dot_colima):
        return dot_colima
    xdg = Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "colima"
    return xdg if exists(xdg) else dot_colima


def _colima_profile(context: str | None) -> str:
    if context and context.startswith("colima-"):
        return context[len("colima-"):]
    return "default"


def _distro(os_release: Callable[[], Mapping[str, str]]) -> tuple[str | None, str | None]:
    try:
        info = os_release()
    except OSError:
        return None, None
    ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
    name = info.get("PRETTY_NAME") or info.get("NAME")
    if ids & {"debian", "ubuntu"}:
        return "debian", name
    if ids & {"fedora", "rhel", "centos"}:
        return "fedora", name
    if "arch" in ids:
        return "arch", name
    return None, name


def _memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None
