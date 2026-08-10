"""Headless coding-agent mutation operator with a soft token budget.

The operator streams the agent's output, accumulates per-step token usage,
and kills the process once the budget is crossed (overshoot by the in-flight
step is accepted) or a wall-clock timeout trips. Two backends: Claude Code
(`claude -p`) and Codex (`codex exec`), invoked headless in the worktree.
"""
from __future__ import annotations

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
    popen=subprocess.Popen,
    monotonic=time.monotonic,
) -> OperatorResult:
    start = monotonic()
    proc = popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                 text=True, bufsize=1)
    total = 0
    reason = "completed"
    for line in proc.stdout:
        total += tokens_from_line(line)
        if total >= token_budget:
            reason = "budget"
            break
        if monotonic() - start >= timeout_s:
            reason = "timeout"
            break
    if reason != "completed":
        proc.terminate()
    proc.wait(timeout=30)
    return OperatorResult(backend=backend, tokens=total, stopped_reason=reason)


def _claude_tokens(line: str) -> int:
    try:
        msg = json.loads(line)
    except ValueError:
        return 0
    usage = (msg.get("message", {}) or {}).get("usage", {}) or msg.get("usage", {})
    return int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))


class ClaudeOperator:
    def __init__(self, *, cli: str = "claude") -> None:
        self._cli = cli

    def run(
        self, worktree: Path, brief: str, *, token_budget: int, timeout_s: float
    ) -> OperatorResult:
        cmd = [self._cli, "-p", brief, "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits"]
        return run_with_token_budget(cmd, worktree, token_budget=token_budget,
                                     timeout_s=timeout_s, tokens_from_line=_claude_tokens,
                                     backend="claude")


def _codex_tokens(line: str) -> int:
    try:
        msg = json.loads(line)
    except ValueError:
        return 0
    usage = msg.get("usage", {}) or {}
    return int(usage.get("total_tokens", 0))


class CodexOperator:
    def __init__(self, *, cli: str = "codex") -> None:
        self._cli = cli

    def run(
        self, worktree: Path, brief: str, *, token_budget: int, timeout_s: float
    ) -> OperatorResult:
        cmd = [self._cli, "exec", brief, "--json", "--full-auto"]
        return run_with_token_budget(cmd, worktree, token_budget=token_budget,
                                     timeout_s=timeout_s, tokens_from_line=_codex_tokens,
                                     backend="codex")
