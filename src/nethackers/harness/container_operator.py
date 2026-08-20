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

from dataclasses import dataclass
from pathlib import Path

from nethackers.harness.operator import _claude_cmd, _codex_cmd


@dataclass(frozen=True)
class ContainerCaps:
    pids: int = 512
    memory: str = "8g"
    cpus: str = "4"
    timeout_s: int = 1800


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
