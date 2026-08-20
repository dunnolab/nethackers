"""Headless coding-agent mutation operator.

Streams the agent's output, meters token usage faithfully (see
``harness.metering``), and reaps the process at EOF. No token budget, no
timeout: the agent runs to completion and is stopped manually (hard kill via
its own process group). Two backends: Claude Code (``claude -p``) and Codex
(``codex exec``), invoked headless in the worktree.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nethackers.harness.metering import Meter, TokenUsage


@dataclass(frozen=True)
class OperatorResult:
    backend: str
    usage: TokenUsage
    stopped_reason: str  # "completed" | "killed"
    returncode: int | None = None
    error_tail: str | None = None

    @property
    def total(self) -> int:
        return self.usage.total


def run_operator(
    cmd: list[str],
    cwd: str | Path,
    *,
    backend: str,
    on_line: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
    popen=subprocess.Popen,
) -> OperatorResult:
    """Stream the operator's stdout, metering faithfully; reap at EOF. No
    budget/timeout -- the agent runs until it exits. The subprocess runs in its
    own session (process group) so a manual stop can hard-kill it and any
    children it spawned: if ``stop`` is set by the caller, the group is killed
    and ``stopped_reason`` is ``"killed"`` (else ``"completed"``). A crashing
    ``on_line`` never aborts the run."""
    meter = Meter(backend)
    proc = popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 text=True, bufsize=1, start_new_session=True)
    err_lines: deque[str] = deque(maxlen=50)

    def _drain_stderr() -> None:
        if getattr(proc, "stderr", None) is None:
            return
        with contextlib.suppress(Exception):
            for line in proc.stderr:
                err_lines.append(line)

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()
    killed = threading.Event()
    done = threading.Event()
    watcher: threading.Thread | None = None
    if stop is not None:
        def _watch() -> None:
            # Poll the shared stop until THIS operator finishes (local `done`).
            # Never touch `stop` itself -- it is shared across every iteration's
            # run, so setting it here would poison it and skip the next iteration.
            while not done.wait(timeout=0.1):
                if stop.is_set():
                    if proc.poll() is None:
                        killed.set()
                        with contextlib.suppress(Exception):
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    return
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
    try:
        for line in proc.stdout:
            if on_line is not None:
                with contextlib.suppress(Exception):
                    on_line(line)
            meter.observe(line)
    finally:
        done.set()  # release the watcher WITHOUT poisoning the shared stop
        if watcher is not None:
            watcher.join(timeout=2)
        err_thread.join(timeout=2)
        with contextlib.suppress(Exception):
            proc.wait(timeout=30)
    error_tail: str | None = None
    with contextlib.suppress(Exception):
        error_tail = "".join(err_lines).strip() or None
    return OperatorResult(backend=backend, usage=meter.usage,
                          stopped_reason="killed" if killed.is_set() else "completed",
                          returncode=getattr(proc, "returncode", None),
                          error_tail=error_tail)


def _claude_cmd(cli: str, brief: str, model: str | None, effort: str | None) -> list[str]:
    # Hermeticity flags: the operator must be a pure function of (parent
    # tree, brief). Claude Code otherwise persists + recalls per-directory
    # memory under ~/.claude/projects/<cwd-slug>/memory across runs that
    # reuse a worktree path -- the confirmed cause of the operator recalling
    # and re-applying its own prior mutation instead of exploring (see
    # docs/superpowers/specs/2026-08-11-hermetic-operator-design.md).
    # --setting-sources drops only the *user* settings layer; auth lives in
    # ~/.claude.json, which is not a setting source, so it still works.
    cmd = [cli, "-p", brief, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits",
           "--settings", '{"autoMemoryEnabled": false}',
           "--setting-sources", "project,local",
           "--strict-mcp-config",
           "--no-session-persistence"]
    if model:
        cmd += ["--model", model]     # pin the model (else Claude Code's default)
    if effort:
        cmd += ["--effort", effort]   # reasoning effort: low|medium|high|xhigh|max
    return cmd


class ClaudeOperator:
    def __init__(self, *, cli: str = "claude", model: str | None = None,
                 effort: str | None = None) -> None:
        self._cli = cli
        self._model = model
        self._effort = effort

    def run(
        self, worktree: Path, brief: str, *,
        on_line: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ) -> OperatorResult:
        return run_operator(_claude_cmd(self._cli, brief, self._model, self._effort),
                            worktree, backend="claude", on_line=on_line, stop=stop)


def _codex_cmd(cli: str, brief: str, model: str | None, effort: str | None) -> list[str]:
    # --skip-git-repo-check is MANDATORY, not hygiene: the operator worktree is
    # a plain shutil.copytree of the elite tree (loop.py -- no .git), and
    # `codex exec` otherwise refuses with "Not inside a trusted directory and
    # --skip-git-repo-check was not specified" on *stderr* -- which run_operator
    # now captures into OperatorResult.error_tail, but without this flag the
    # mutation would still silently no-op (no changes, gate sees child ==
    # parent). `claude -p` has no such requirement, which is why only the
    # codex operator was affected.
    #
    # The rest are consistency + hygiene: codex has no auto-memory recall and
    # `codex exec` never auto-resumes, but --ephemeral stops writing
    # session/rollout files and --ignore-user-config/--ignore-rules drop
    # inherited config/rules so the operator stays a pure function of (parent
    # tree, brief). Auth still works -- --ignore-user-config only drops
    # $CODEX_HOME/config.toml.
    cmd = [cli, "exec", brief, "--json", "--full-auto", "--skip-git-repo-check",
           "--ephemeral", "--ignore-user-config", "--ignore-rules"]
    # --ignore-user-config drops ~/.codex/config.toml -- including its `model`
    # and `model_reasoning_effort` -- so pin them back explicitly here (a `-c`
    # override still applies on top of --ignore-user-config).
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    return cmd


class CodexOperator:
    def __init__(self, *, cli: str = "codex", model: str | None = None,
                 effort: str | None = None) -> None:
        self._cli = cli
        self._model = model
        self._effort = effort

    def run(
        self, worktree: Path, brief: str, *,
        on_line: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ) -> OperatorResult:
        return run_operator(_codex_cmd(self._cli, brief, self._model, self._effort),
                            worktree, backend="codex", on_line=on_line, stop=stop)
