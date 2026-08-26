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

    @property
    def total(self) -> int:
        return self.usage.total

    @property
    def spend(self) -> int:
        return self.usage.spend


def run_operator(
    cmd: list[str],
    cwd: str | Path,
    *,
    backend: str,
    on_line: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
    refs: Path | None = None,
    popen=subprocess.Popen,
) -> OperatorResult:
    """Stream the operator's stdout, metering faithfully; reap at EOF. No
    budget/timeout -- the agent runs until it exits. The subprocess runs in its
    own session (process group) so a manual stop can hard-kill it and any
    children it spawned: if ``stop`` is set by the caller, the group is killed
    and ``stopped_reason`` is ``"killed"`` (else ``"completed"``). A crashing
    ``on_line`` never aborts the run. A non-zero backend exit is surfaced as an
    error with the useful tail of its combined stdout/stderr.

    ``refs`` is accepted but unused: it exists only for interface parity with
    ``ContainerOperator.run``, which bind-mounts it into the sandbox. This
    (host) path is not the real path -- real runs go through
    ``ContainerOperator`` -- so there is no host directory to mount it into;
    a caller that treats both operator paths uniformly can still pass
    ``refs=`` here without a branch.
    """
    meter = Meter(backend)
    # Keep stderr in the same stream as the backend's JSONL output.  CLI parse
    # and startup failures are written only to stderr; discarding it used to
    # make them look like successful zero-token no-ops.
    proc = popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, bufsize=1, start_new_session=True)
    killed = threading.Event()
    done = threading.Event()
    output_tail: deque[str] = deque(maxlen=20)
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
            output_tail.append(line.rstrip())
            if on_line is not None:
                with contextlib.suppress(Exception):
                    on_line(line)
            meter.observe(line)
    finally:
        done.set()  # release the watcher WITHOUT poisoning the shared stop
        if watcher is not None:
            watcher.join(timeout=2)
        try:
            returncode = proc.wait(timeout=30)
        except Exception:
            returncode = None
    if returncode not in (None, 0) and not killed.is_set():
        detail = next(
            (line for line in output_tail if line.lstrip().lower().startswith("error:")),
            next((line for line in reversed(output_tail) if line.strip()), "no output"),
        )
        raise RuntimeError(f"{backend} operator exited with status {returncode}: {detail}")
    return OperatorResult(backend=backend, usage=meter.usage,
                          stopped_reason="killed" if killed.is_set() else "completed")


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


def _codex_cmd(cli: str, brief: str, model: str | None, effort: str | None) -> list[str]:
    # --skip-git-repo-check is MANDATORY, not hygiene: the operator worktree is
    # a plain shutil.copytree of the elite tree (loop.py -- no .git), and
    # `codex exec` otherwise refuses with "Not inside a trusted directory and
    # --skip-git-repo-check was not specified" on *stderr*. Before operator
    # failures were surfaced above, that silently no-op'd and the gate saw the
    # child as identical to its parent. `claude -p` has no such requirement.
    #
    # The rest are consistency + hygiene: codex has no auto-memory recall and
    # `codex exec` never auto-resumes, but --ephemeral stops writing
    # session/rollout files and --ignore-user-config/--ignore-rules drop
    # inherited config/rules so the operator stays a pure function of (parent
    # tree, brief). Auth still works -- --ignore-user-config only drops
    # $CODEX_HOME/config.toml.
    cmd = [cli, "exec", brief, "--json", "--approve-for-me", "--skip-git-repo-check",
           "--ephemeral", "--ignore-user-config", "--ignore-rules"]
    # --ignore-user-config drops ~/.codex/config.toml -- including its `model`
    # and `model_reasoning_effort` -- so pin them back explicitly here (a `-c`
    # override still applies on top of --ignore-user-config).
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    return cmd
