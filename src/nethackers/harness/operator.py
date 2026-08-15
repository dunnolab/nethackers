"""Headless coding-agent mutation operator with a soft token budget.

The operator streams the agent's output, accumulates per-step token usage,
and kills the process once the budget is crossed (overshoot by the in-flight
step is accepted) or a wall-clock timeout trips. Two backends: Claude Code
(`claude -p`) and Codex (`codex exec`), invoked headless in the worktree.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OperatorResult:
    backend: str
    tokens: int
    stopped_reason: str  # "completed" | "budget" | "timeout"


def run_with_token_budget(
    cmd: list[str],
    cwd: str | Path,
    *,
    token_budget: int,
    timeout_s: float,
    tokens_from_line: Callable[[str], int],
    backend: str,
    on_line: Callable[[str], None] | None = None,
    popen=subprocess.Popen,
    monotonic=time.monotonic,
) -> OperatorResult:
    start = monotonic()
    proc = popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                 text=True, bufsize=1)
    total = 0
    reason = "completed"
    for line in proc.stdout:
        if on_line is not None:
            with contextlib.suppress(Exception):
                on_line(line)
        total += tokens_from_line(line)
        if total >= token_budget:
            reason = "budget"
            break
        if monotonic() - start >= timeout_s:
            reason = "timeout"
            break
    if reason != "completed":
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return OperatorResult(backend=backend, tokens=total, stopped_reason=reason)


def _usage_tokens(usage: object, *keys: str) -> int:
    """Sum ``keys`` from a usage dict, tolerating any non-dict / null / non-int
    value the agent's stream might emit -- a stray line must never crash the
    whole mutation. (This is what the "'str' object has no attribute 'get'"
    crash was: a Claude stream line whose ``message`` field was a string.)"""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _claude_tokens(line: str) -> int:
    try:
        msg = json.loads(line)
    except ValueError:
        return 0
    if not isinstance(msg, dict):
        return 0
    inner = msg.get("message")
    usage = inner.get("usage") if isinstance(inner, dict) else msg.get("usage")
    return _usage_tokens(usage, "input_tokens", "output_tokens")


def _claude_cmd(cli: str, brief: str) -> list[str]:
    # Hermeticity flags: the operator must be a pure function of (parent
    # tree, brief). Claude Code otherwise persists + recalls per-directory
    # memory under ~/.claude/projects/<cwd-slug>/memory across runs that
    # reuse a worktree path -- the confirmed cause of the operator recalling
    # and re-applying its own prior mutation instead of exploring (see
    # docs/superpowers/specs/2026-08-11-hermetic-operator-design.md).
    # --setting-sources drops only the *user* settings layer; auth lives in
    # ~/.claude.json, which is not a setting source, so it still works.
    return [cli, "-p", brief, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--settings", '{"autoMemoryEnabled": false}',
            "--setting-sources", "project,local",
            "--strict-mcp-config",
            "--no-session-persistence"]


class ClaudeOperator:
    def __init__(self, *, cli: str = "claude") -> None:
        self._cli = cli

    def run(
        self, worktree: Path, brief: str, *, token_budget: int, timeout_s: float,
        on_line: Callable[[str], None] | None = None
    ) -> OperatorResult:
        cmd = _claude_cmd(self._cli, brief)
        return run_with_token_budget(cmd, worktree, token_budget=token_budget,
                                     timeout_s=timeout_s, tokens_from_line=_claude_tokens,
                                     backend="claude", on_line=on_line)


def _codex_tokens(line: str) -> int:
    try:
        msg = json.loads(line)
    except ValueError:
        return 0
    if not isinstance(msg, dict):
        return 0
    return _usage_tokens(msg.get("usage"), "total_tokens")


def agent_tokens(backend: str, line: str) -> int:
    """Public dispatch used by the UI to count tokens from the same stream it
    already receives for the mutation log. Unknown backend -> 0."""
    if backend == "claude":
        return _claude_tokens(line)
    if backend == "codex":
        return _codex_tokens(line)
    return 0


def _codex_cmd(cli: str, brief: str) -> list[str]:
    # --skip-git-repo-check is MANDATORY, not hygiene: the operator worktree is
    # a plain shutil.copytree of the elite tree (loop.py -- no .git), and
    # `codex exec` otherwise refuses with "Not inside a trusted directory and
    # --skip-git-repo-check was not specified" on *stderr* -- which
    # run_with_token_budget routes to DEVNULL, so the mutation silently no-ops
    # (0 tokens, no changes, gate sees child == parent). `claude -p` has no
    # such requirement, which is why only the codex operator was affected.
    #
    # The rest are consistency + hygiene: codex has no auto-memory recall and
    # `codex exec` never auto-resumes, but --ephemeral stops writing
    # session/rollout files and --ignore-user-config/--ignore-rules drop
    # inherited config/rules so the operator stays a pure function of (parent
    # tree, brief). Auth still works -- --ignore-user-config only drops
    # $CODEX_HOME/config.toml.
    return [cli, "exec", brief, "--json", "--full-auto", "--skip-git-repo-check",
            "--ephemeral", "--ignore-user-config", "--ignore-rules"]


class CodexOperator:
    def __init__(self, *, cli: str = "codex") -> None:
        self._cli = cli

    def run(
        self, worktree: Path, brief: str, *, token_budget: int, timeout_s: float,
        on_line: Callable[[str], None] | None = None
    ) -> OperatorResult:
        cmd = _codex_cmd(self._cli, brief)
        return run_with_token_budget(cmd, worktree, token_budget=token_budget,
                                     timeout_s=timeout_s, tokens_from_line=_codex_tokens,
                                     backend="codex", on_line=on_line)
