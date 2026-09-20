"""Build the ``docker run …`` argv that wraps a host coding-agent CLI inside a
resource-capped, no-new-privileges container -- the mutator's execution cage.

Cgroup caps (``--pids-limit``/``--memory``/``--cpus``) are the fork-bomb /
runaway-process defense the host operator (``harness/operator.py``) lacks;
``--memory-swap`` is pinned to the same value as ``--memory`` so a runaway
can't just push into swap and reach ~2x the intended cap (Docker's default
otherwise allows exactly that). An in-container wall-clock ``timeout``
backstops a wedged harness. The in-cage harness command reuses the existing
host argv builders so all three harnesses stay a single source of truth, with
each harness's own confirmation-prompt
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

from nethackers.containers import label_args
from nethackers.harness.auth_inject import auth_docker_args
from nethackers.harness.operator import (
    OperatorResult,
    _claude_cmd,
    _codex_cmd,
    _opencode2_cmd,
    run_operator,
)
from nethackers.harness.sandbox_preflight import mutator_platform_args


@dataclass(frozen=True)
class ContainerCaps:
    pids: int = 512
    memory: str = "8g"
    cpus: str = "4"
    timeout_s: int = 28800  # 8h: a real mutation may experiment against live NLE
                            # for a long time; this is a runaway ceiling, not a
                            # target -- the agent exits (and the container is
                            # reaped) as soon as it finishes.


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
    refs: Path | None = None,
    docker: str = "docker",
    userns_args: list[str] | None = None,
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

    ``userns_args`` are extra uid-mapping ``run`` flags, supplied by
    ``containers.nonroot_userns_args`` (which probes the runtime to decide) and
    merely carried here, so this stays pure argv assembly. Empty/``None`` --
    docker and rootful podman -- keeps the argv byte-identical to before the
    option existed; under ROOTLESS podman it is what stops the entrypoint's
    drop to ``agent`` from landing on a ``/workspace`` it can't write (#54).

    ``refs``, when given, is bind-mounted read-only at ``/refs`` -- the
    reference folders ``refs.assemble`` (a separate task) builds for the
    coding agent to read from inside the sandbox. ``None`` (the default)
    keeps the argv byte-identical to before this mount existed, so callers
    that don't yet have a refs dir (and all pre-existing tests) are unaffected.
    """
    argv = [
        docker, "run", *mutator_platform_args(image), "--rm",
        "--name", name, *label_args(),
        "--pids-limit", str(caps.pids),
        "--memory", caps.memory,
        "--memory-swap", caps.memory,  # cap swap too -- else a runaway reaches ~2x via swap
        "--cpus", caps.cpus,
        "--security-opt", "no-new-privileges",
        *(userns_args or []),
        "-v", f"{worktree}:/workspace",
    ]
    if refs is not None:
        argv += ["-v", f"{refs}:/refs:ro"]
    argv += ["-w", "/workspace"]
    if harness == "opencode2":
        argv += ["-i"]   # the brief arrives on stdin (see _opencode2_cmd)
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
    if harness == "opencode2":
        return _opencode2_cmd(cli or "opencode2", model, effort)
    raise ValueError(f"unknown harness: {harness!r}")


def _mutator_container_name(
    run_id: str | None, worktree_name: str, parent_name: str | None = None,
) -> str:
    """`nethackers-mut-${RUN_ID}-${ITER}` (spec §3.3), prefixed per INV13 so
    every container we start is ``docker ps -f name=nethackers-``-filterable.
    Falls back to ``parent_name`` when no run id is available (existing unit
    tests / callers that don't yet have one) -- see ``ContainerOperator.run``'s
    caller for why the run id is preferred over the worktree-derived form."""
    stem = run_id if run_id is not None else parent_name
    return f"nethackers-mut-{stem}-{worktree_name}"


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
        run_id: str | None = None,
        userns_args: list[str] | None = None,
    ) -> None:
        self.harness = harness
        self.image = image
        self.cli = cli
        self.model = model
        self.effort = effort
        self.caps = caps
        # Threaded into the container name (see `run`) so it satisfies spec
        # §3.3's `nethackers-mut-${RUN_ID}-${ITER}` -- `worktree.parent.name`
        # alone is ALWAYS the literal "work" (worktree == <workdir>/runs/<rid>/
        # work/iter-N), so without this every run would collide on the same
        # name (e.g. `nethackers-mut-work-iter-0`), racing on `docker run
        # --name` and letting a hard-stop's `docker kill` hit the wrong run's
        # live container.
        # None keeps the old worktree-derived form as a fallback, so callers
        # that don't (yet) have a run id -- e.g. existing unit tests -- still
        # work; every real caller (launch.py) always passes one.
        self._run_id = run_id
        # Resolved once at construction (not per-run): callers that care
        # about the real host (production) leave these unset; tests inject a
        # fake system/home so the auth preflight below never touches the
        # actual filesystem or Keychain.
        self.system = system if system is not None else platform.system()
        self.home = home if home is not None else Path.home()
        self.docker = docker
        # Resolved by the CALLER (launch.py, via containers.nonroot_userns_args)
        # once per run, not probed here per iteration -- same "resolve once at
        # the entry point, thread it down" rule as `docker` itself, and it keeps
        # constructing an operator subprocess-free for tests. Empty on docker
        # and rootful podman.
        self.userns_args = list(userns_args or [])
        self._popen = subprocess.Popen

    def run(
        self,
        worktree: Path,
        brief: str,
        *,
        refs: Path | None = None,
        on_line: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ) -> OperatorResult:
        # `worktree.name` is already `iter-N`; prefer the run id (spec §3.3:
        # `nethackers-mut-${RUN_ID}-${ITER}`) so two runs never collide on the
        # same container name -- see the __init__ comment on `self._run_id`.
        name = _mutator_container_name(
            self._run_id, worktree.name, worktree.parent.name
        )
        # Resolve auth/config BEFORE shelling out to docker: login-only
        # backends fail early instead of mounting an empty path. OpenCode 2
        # never fails here (no key still leaves its free models), and never
        # reads the worktree's config: that tree is untrusted.
        # AuthUnavailable propagates to the caller (the CLI catches it).
        auth = auth_docker_args(
            self.harness, system=self.system, home=self.home, _require_exists=True,
        )
        argv = build_docker_argv(
            harness=self.harness, image=self.image, name=name, worktree=worktree,
            cli=self.cli, model=self.model, effort=self.effort, caps=self.caps,
            auth_args=auth, brief=brief, refs=refs, docker=self.docker,
            userns_args=self.userns_args,
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
                stdin_text=brief if self.harness == "opencode2" else None,
            )
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=2)
            # Closes a race between the two watchers (this one, and
            # run_operator's own internal one -- both poll the same `stop`
            # but against different `done` events): if run_operator's own
            # watcher is the one that notices `stop` first, it SIGKILLs only
            # the local `docker run` client, run_operator returns fast, and
            # THIS `done` gets set -- possibly while our watcher is still
            # mid-wait, so its `done.wait()` returns via the "already set"
            # path without ever reaching the `stop.is_set()` check above,
            # and the container is left running (SIGKILLing the client does
            # NOT stop it). This final call is synchronous, on the main
            # thread, made only after run_operator is confirmed done --
            # independent of watcher poll-timing, so it can't lose the race.
            # `_maybe_kill_on_stop` already no-ops when `stop` was never set
            # (normal completion), and `_docker_kill` is idempotent/
            # best-effort, so a possible double-call (watcher + this) is
            # harmless.
            self._maybe_kill_on_stop(name, stop)

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
