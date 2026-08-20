"""Build the ``docker run …`` argv that wraps a host coding-agent CLI inside a
resource-capped, no-new-privileges container -- the mutator's execution cage.

Cgroup caps (``--pids-limit``/``--memory``/``--cpus``) are the fork-bomb /
runaway-process defense the host operator (``harness/operator.py``) lacks;
``--memory-swap`` is pinned to the same value as ``--memory`` so a runaway
can't just push into swap and reach ~2x the intended cap (Docker's default
otherwise allows exactly that). An in-container wall-clock ``timeout``
backstops a wedged harness. The in-cage harness command reuses the existing
host argv builders (``_claude_cmd`` / ``_codex_cmd``) so the two harnesses
stay a single source of truth, with each harness's own confirmation-prompt
escape hatch swapped for the equivalent that's safe to use *because* the
container is already the sandbox: codex's
``--approve-for-me`` (host-side "don't ask me") is replaced with
``--dangerously-bypass-approvals-and-sandbox`` (codex's own inner sandbox
would otherwise double-sandbox and fail inside the container); claude gets
``--dangerously-skip-permissions`` appended outright, since claude has no
"already sandboxed" flag of its own.
"""
from __future__ import annotations

import contextlib
import platform
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nethackers.harness.auth_inject import auth_docker_args
from nethackers.harness.operator import OperatorResult, _claude_cmd, _codex_cmd, run_operator


@dataclass(frozen=True)
class ContainerCaps:
    pids: int = 512
    memory: str = "8g"
    cpus: str = "4"
    timeout_s: int = 1800


# Module-level singleton, not a `ContainerCaps()` call in ContainerOperator's
# own signature (ruff B008): safe to share because ContainerCaps is frozen.
_DEFAULT_CAPS = ContainerCaps()


def build_docker_argv(
    *,
    harness: str,
    image: str,
    name: str,
    worktree: Path,
    cli: str | None,
    model: str | None,
    effort: str | None,
    caps: ContainerCaps,
    auth_args: list[str],
    brief: str,
) -> list[str]:
    """Assemble ``docker run`` argv for one mutator iteration: fixed docker
    prefix (name + caps + security-opt + workspace mount), then ``auth_args``,
    then ``image``, then an in-container wall-clock ``timeout``, then the
    in-cage harness command.

    ``brief`` flows straight into the inner ``_claude_cmd``/``_codex_cmd``
    call and is required (no placeholder default): a caller that forgets it
    would otherwise silently build a wrong-prompt argv instead of failing
    loudly. Callers that only care about argv shape (this module's own tests)
    pass a fixed ``brief="B"``; the operator that wraps this function (a
    later task) passes the real per-iteration brief.
    """
    argv = [
        "docker", "run", "--rm",
        "--name", name,
        "--pids-limit", str(caps.pids),
        "--memory", caps.memory,
        "--memory-swap", caps.memory,  # cap swap too -- else a runaway reaches ~2x via swap
        "--cpus", caps.cpus,
        "--security-opt", "no-new-privileges",
        "-v", f"{worktree}:/workspace",
        "-w", "/workspace",
    ]
    argv += auth_args
    argv += [image]
    argv += ["timeout", str(caps.timeout_s)]
    argv += _in_cage_cmd(harness, cli, brief, model, effort)
    return argv


def _in_cage_cmd(
    harness: str, cli: str | None, brief: str, model: str | None, effort: str | None,
) -> list[str]:
    if harness == "claude":
        return _claude_cmd(cli or "claude", brief, model, effort) + [
            "--dangerously-skip-permissions",
        ]
    if harness == "codex":
        cmd = _codex_cmd(cli or "codex", brief, model, effort)
        return [
            "--dangerously-bypass-approvals-and-sandbox" if tok == "--approve-for-me" else tok
            for tok in cmd
        ]
    raise ValueError(f"unknown harness: {harness!r}")


class ContainerOperator:
    """A drop-in operator backend alongside ``operator.ClaudeOperator`` /
    ``CodexOperator`` -- same interface (``run(worktree, brief, *, on_line,
    stop)``), same ``OperatorResult`` contract -- that runs the chosen
    ``harness`` inside a resource-capped container instead of on the host.

    Streaming and metering are reused wholesale via ``run_operator``; only
    two things differ from the host path: the argv is ``build_docker_argv``'s
    ``docker run …`` wrapper instead of a bare harness command, and ``stop``
    must additionally ``docker kill`` the named container -- SIGKILLing the
    local ``docker run`` client (what ``run_operator``'s own stop-watcher
    already does) leaves the daemon-managed container running, since the
    client is only a foreground attach to it.
    """

    def __init__(
        self,
        *,
        harness: str,
        image: str,
        cli: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        caps: ContainerCaps = _DEFAULT_CAPS,
        system: str | None = None,
        home: Path | None = None,
        docker: str = "docker",
    ) -> None:
        self.harness = harness
        self.image = image
        self.cli = cli
        self.model = model
        self.effort = effort
        self.caps = caps
        # Resolved once at construction (not per-run): callers that care
        # about the real host (production) leave these unset; tests inject a
        # fake system/home so the auth preflight below never touches the
        # actual filesystem or Keychain.
        self.system = system if system is not None else platform.system()
        self.home = home if home is not None else Path.home()
        self.docker = docker
        self._popen = subprocess.Popen

    def run(
        self,
        worktree: Path,
        brief: str,
        *,
        on_line: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ) -> OperatorResult:
        name = f"mut-{worktree.parent.name}-{worktree.name}"
        # Preflight BEFORE shelling out to docker at all: a not-logged-in
        # host would otherwise have docker silently bind-mount an empty dir
        # (auth_docker_args is pure string formatting by default) and the
        # in-container harness would fail confusingly, deep inside the run.
        # AuthUnavailable propagates to the caller (the CLI catches it).
        auth = auth_docker_args(
            self.harness, system=self.system, home=self.home, _require_exists=True,
        )
        argv = build_docker_argv(
            harness=self.harness, image=self.image, name=name, worktree=worktree,
            cli=self.cli, model=self.model, effort=self.effort, caps=self.caps,
            auth_args=auth, brief=brief,
        )
        done = threading.Event()
        watcher: threading.Thread | None = None
        if stop is not None:
            def _watch() -> None:
                # Poll the shared `stop` until THIS run finishes (local
                # `done`) -- mirrors run_operator's own watcher shape so a
                # normal completion never blocks on, or poisons, `stop`
                # (shared across every iteration's run).
                while not done.wait(timeout=0.1):
                    if stop.is_set():
                        self._maybe_kill_on_stop(name, stop)
                        return
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()
        try:
            # `stop` is also passed straight through so run_operator does its
            # own SIGKILL of the local `docker run` client (reaping the
            # foreground process + the "killed" bookkeeping below); the
            # watcher above is what actually stops the container.
            return run_operator(
                argv, cwd=".", backend=self.harness, on_line=on_line, stop=stop,
                popen=self._popen,
            )
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=2)

    def _maybe_kill_on_stop(self, name: str, stop: threading.Event | None) -> None:
        """Single check-and-act step, kept separate from the watcher's poll
        loop so it is unit-drivable: if ``stop`` is set, ``docker kill`` the
        named container. ``run``'s background thread calls this every poll
        tick; tests call it once directly with an already-set ``stop``."""
        if stop is not None and stop.is_set():
            self._docker_kill(name)

    def _docker_kill(self, name: str) -> None:
        """``docker kill`` the named container. Best-effort: a container that
        already exited on its own (``--rm`` reaped it) makes this fail
        harmlessly, which is fine -- there is nothing left to stop."""
        with contextlib.suppress(Exception):
            subprocess.run([self.docker, "kill", name], capture_output=True)
