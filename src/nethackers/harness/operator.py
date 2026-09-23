"""Headless coding-agent mutation operator.

Streams the agent's output, meters token usage faithfully (see
``harness.metering``), and reaps the process at EOF. No token budget, no
timeout: the agent runs to completion and is stopped manually (hard kill via
its own process group). Backends are Claude Code (``claude -p``), Codex
(``codex exec``), and OpenCode 2 (``opencode2 run``), invoked headless in the
worktree.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
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


class OperatorRefused(RuntimeError):
    """The backend rejected the request itself, so retrying cannot help.

    A wrong `--model` is the case that motivated this: the CLI resolves the id
    against its OWN allowlist, so claude 2.1.270 answered `--model
    claude-opus-5-5` with `[claude-code:unrecognized_model]` and exited 1 in
    under a second, having spent nothing. The loop's circuit breaker treated
    that like a flaky operator and burned three iterations on it. Nothing about
    a rejected model changes between attempts, so the loop stops on the first
    one.
    """


# The claude CLI's own machine-readable complaint, e.g.
# `[claude-code:unrecognized_model] {"model":"claude-opus-5-5",...}` -- emitted
# as the FIRST line and followed by the usual result JSON, so the old
# "last non-empty line" fallback surfaced a 900-character usage blob instead.
_CLAUDE_DIAG = re.compile(r"\[claude-code:([a-z_]+)\]\s*(\{.*\})?")


def _error_detail(output_tail: Sequence[str]) -> tuple[str, bool]:
    """(what to report, whether retrying is pointless) for a failed operator."""
    for line in output_tail:
        m = _CLAUDE_DIAG.search(line)
        if m is None:
            continue
        code = m.group(1)
        if code == "unrecognized_model":
            model = ""
            with contextlib.suppress(Exception):
                model = json.loads(m.group(2) or "{}").get("model", "")
            named = f" '{model}'" if model else ""
            return (f"unrecognized model{named} — the sandbox's CLI doesn't know that "
                    f"id; use an alias like `opus`, or update the CLI in the mutator "
                    f"image", True)
        return (f"{code.replace('_', ' ')} (the backend refused the request)", True)
    for line in output_tail:
        if line.lstrip().lower().startswith("error:"):
            return line.strip(), False
    for line in reversed(output_tail):
        # the trailing result JSON is a usage dump, not a diagnosis -- skip it
        # when anything more specific is left.
        if line.strip() and not line.lstrip().startswith('{"'):
            return line.strip(), False
    tail = next((line for line in reversed(output_tail) if line.strip()), "no output")
    return tail, False


def run_operator(
    cmd: list[str],
    cwd: str | Path,
    *,
    backend: str,
    on_line: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
    refs: Path | None = None,
    popen=subprocess.Popen,
    stdin_text: str | None = None,
) -> OperatorResult:
    """Stream the operator's stdout, metering faithfully; reap at EOF. No
    budget/timeout -- the agent runs until it exits. The subprocess runs in its
    own session (process group) so a manual stop can hard-kill it and any
    children it spawned: if ``stop`` is set by the caller, the group is killed
    and ``stopped_reason`` is ``"killed"`` (else ``"completed"``). A crashing
    ``on_line`` never aborts the run. A non-zero backend exit is surfaced as an
    error with the useful tail of its combined stdout/stderr.

    ``stdin_text``, when given, is written to the process's stdin, which is then
    closed -- for a backend that takes its prompt there (OpenCode 2). Otherwise
    stdin is left untouched.

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
    stdin_kw = {} if stdin_text is None else {"stdin": subprocess.PIPE}
    proc = popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, bufsize=1, start_new_session=True, **stdin_kw)
    if stdin_text is not None:
        # A process that dies before reading reports its own failure below.
        with contextlib.suppress(BrokenPipeError, OSError):
            proc.stdin.write(stdin_text)
        with contextlib.suppress(BrokenPipeError, OSError):
            proc.stdin.close()
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
        detail, refused = _error_detail(output_tail)
        message = f"{backend} operator exited with status {returncode}: {detail}"
        raise OperatorRefused(message) if refused else RuntimeError(message)
    return OperatorResult(backend=backend, usage=meter.usage,
                          stopped_reason="killed" if killed.is_set() else "completed")


def _claude_cmd(cli: str, brief: str, model: str | None, effort: str | None) -> list[str]:
    # Hermeticity flags: the operator must be a pure function of (parent
    # tree, brief). Claude Code otherwise persists + recalls per-directory
    # memory under ~/.claude/projects/<cwd-slug>/memory across runs that
    # reuse a worktree path -- the confirmed cause of the operator recalling
    # and re-applying its own prior mutation instead of exploring.
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


def _opencode2_cmd(cli: str, model: str | None, effort: str | None) -> list[str]:
    """Build a fresh, non-interactive OpenCode run.

    The brief is not an argument: ``opencode run`` wraps a message containing
    spaces in quotes and escapes its inner quotes, which broke the brief's
    JSON commands. The caller writes it to stdin, which OpenCode reads to EOF
    verbatim. The mutator container supplies the isolation boundary, so
    ``--auto`` lets OpenCode use its tools without stopping for approval. Its
    data directory is ephemeral (nothing is mounted over it), so sessions
    carry no memory between iterations. OpenCode calls its provider-specific
    reasoning setting a model ``variant``.

    (``cli`` is ``opencode2`` in the sandbox -- a symlink to the ``opencode``
    binary, kept to preserve the operator id across the upstream rename; see
    Dockerfile.mutator.)
    """
    # `--thinking` is required even in JSON mode: without it OpenCode consumes
    # provider reasoning blocks but omits them from the event stream, leaving
    # the mutation log with tool calls only.  The formatter already renders
    # emitted `reasoning` events, so opt in explicitly for parity with the
    # reasoning traces shown by the Codex and Claude backends.
    #
    # `--standalone` is gone in opencode-ai@1.x; `run` is standalone already.
    cmd = [cli, "run", "--format", "json", "--thinking", "--auto"]
    if model:
        # The reasoning variant is now a dedicated `--variant` flag, not the
        # `provider/model#variant` suffix the beta encoded it into. Strip any
        # legacy suffix off the model, and prefer an explicit `effort` over one
        # embedded in the model ref. Variant only rides alongside a pinned
        # model -- the CLI/TUI reject `--effort` without one.
        base, _, embedded = model.partition("#")
        cmd += ["--model", base]
        variant = effort or embedded
        if variant:
            cmd += ["--variant", variant]
    return cmd


def _codex_cmd(
    cli: str, brief: str, model: str | None, effort: str | None,
    *, broker_base: str | None = None,
) -> list[str]:
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
    # and `model_reasoning_effort` -- so pin them back explicitly here. A `-c`
    # INVOCATION override still applies on top of --ignore-user-config (which
    # only discards the config FILE, not `-c` flags) -- that is exactly the
    # property the broker path below relies on.
    if model:
        cmd += ["-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    # Credential-broker routing (broker path only; None keeps the mount path's
    # argv byte-identical). `codex exec --ignore-user-config` DISCARDS
    # ~/.codex/config.toml, so a cage `openai_base_url` there is silently
    # ignored -- codex falls back to api.openai.com and 401s. The fix (OpenAI's
    # own responses-api-proxy recipe) is a `-c` override -- which survives
    # --ignore-user-config -- pointing codex at a CUSTOM provider. With no
    # `requires_openai_auth`/`env_key`, codex uses its unauthenticated auth
    # provider and sends the POST with NO Authorization header, so the broker
    # injects 100% of the auth host-side and no credential ever enters the box.
    # `base_url` ends in /backend-api/codex, so codex POSTs
    # `<broker_base>/backend-api/codex/responses` and the broker forwards
    # `upstream + path` = chatgpt.com + /backend-api/codex/responses.
    # `supports_websockets = false` avoids codex's WS-first attempt. The `-c`
    # value is parsed as TOML; an inline table is the single-line form.
    if broker_base is not None:
        cmd += [
            "-c", "model_provider=nethackers-broker",
            "-c", (
                'model_providers.nethackers-broker={ name = "nethackers-broker", '
                'base_url = "' + broker_base + '/backend-api/codex", '
                'wire_api = "responses", supports_websockets = false }'
            ),
        ]
    return cmd
