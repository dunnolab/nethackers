"""Pure conversion of a coding-agent stream line into display lines for the
Mutation-logs tab. Never raises: any unparseable / unknown shape yields []
(the same defensive discipline as metering.classify)."""
from __future__ import annotations

import json
import os
import re

PrettyLine = tuple[str, str]  # (kind, text); kind in {assistant, tool, result, meta, brief}

# codex wraps every shell action as `/bin/zsh -lc "<actual command>"`; show the
# actual command, not the wrapper.
_SHELL_WRAP = re.compile(r"^\S*sh\s+-[a-z]*c\s+(.*)$", re.DOTALL)

_CLAUDE_VERB = {"Edit": "edit", "MultiEdit": "edit", "Write": "write",
                "Read": "read", "Bash": "bash"}


def _out_tokens(usage: object) -> int:
    """Defensive output-token read for the result line (tolerates any shape)."""
    if not isinstance(usage, dict):
        return 0
    value = usage.get("output_tokens")
    return int(value) if isinstance(value, (int, float)) else 0


def _claude(obj: dict) -> list[PrettyLine]:
    kind = obj.get("type")
    if kind == "assistant":
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            return []
        out: list[PrettyLine] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    out.append(("assistant", text.strip()))
            elif bt == "tool_use":
                name = str(block.get("name", ""))
                verb = _CLAUDE_VERB.get(name, name.lower())
                input_obj = block.get("input")
                inp = input_obj if isinstance(input_obj, dict) else {}
                arg = inp.get("file_path") or inp.get("command") or inp.get("path") or ""
                out.append(("tool", f"{verb} {arg}".strip()))
        return out
    if kind == "result":
        parts = ["done"]
        sub = obj.get("subtype")
        if isinstance(sub, str) and sub:
            parts.append(sub)
        tok = _out_tokens(obj.get("usage"))
        if tok:
            parts.append(f"{tok:,} tok")
        return [("result", " · ".join(parts))]
    return []


def _codex_command(command: str) -> str:
    """The inner shell command, unwrapped from codex's `/bin/zsh -lc "..."`."""
    inner = command.strip()
    match = _SHELL_WRAP.match(inner)
    if match:
        inner = match.group(1).strip()
    if len(inner) >= 2 and inner[0] in "\"'" and inner[-1] == inner[0]:
        inner = inner[1:-1].strip()
    return inner


def _codex_edit(changes: object) -> str:
    """`edit <basename[, ...]>` from a file_change's ``changes`` list, or ``""``."""
    if not isinstance(changes, list):
        return ""
    names = [os.path.basename(c["path"]) for c in changes
             if isinstance(c, dict) and isinstance(c.get("path"), str) and c["path"]]
    return "edit " + ", ".join(names) if names else ""


def _codex(obj: dict) -> list[PrettyLine]:
    # codex-cli >=0.1x streams item-lifecycle events with the payload nested
    # under "item"; each item fires on both item.started and item.completed, so
    # render each exactly once: commands/edits when they start (live feedback),
    # the agent's messages when they complete. Never raises; [] on any unknown
    # shape. The top-level {text,message,command} fallback keeps older codex
    # (and any future flattened event) working.
    item = obj.get("item")
    if isinstance(item, dict):
        kind, itype = obj.get("type"), item.get("type")
        if kind == "item.started" and itype == "command_execution":
            cmd = item.get("command")
            if isinstance(cmd, str) and cmd.strip():
                return [("tool", _codex_command(cmd))]
        if kind == "item.started" and itype == "file_change":
            edit = _codex_edit(item.get("changes"))
            if edit:
                return [("tool", edit)]
        if kind == "item.completed" and itype == "agent_message":
            msg = item.get("text")
            if isinstance(msg, str) and msg.strip():
                return [("assistant", msg.strip())]
        return []
    text = obj.get("text") or obj.get("message")
    if isinstance(text, str) and text.strip():
        return [("assistant", text.strip())]
    cmd = obj.get("command")
    if isinstance(cmd, str) and cmd.strip():
        return [("tool", f"bash {cmd.strip()}")]
    return []


def _opencode2(obj: dict) -> list[PrettyLine]:
    """Render OpenCode 2's `run --format json` event stream."""
    kind = obj.get("type")

    # Provider/configuration failures are top-level events rather than parts.
    if kind == "error":
        error = obj.get("error")
        message = (error.get("message") or error.get("name")
                   if isinstance(error, dict) else error)
        if isinstance(message, str) and message.strip():
            return [("result", f"error: {message.strip()}")]
        return [("result", "error")]

    part = obj.get("part")
    if not isinstance(part, dict):
        return []

    # Some providers expose reasoning text and some do not. Render only the
    # reasoning OpenCode actually streams; never infer or manufacture it.
    part_kind = part.get("type")
    if kind in {"reasoning", "reasoning_delta"} or part_kind in {
        "reasoning", "reasoning-delta",
    }:
        reasoning = part.get("text") or part.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return [("meta", f"thinking: {reasoning.strip()}")]
    if kind == "text":
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            return [("assistant", text.strip())]
    if kind == "tool_use":
        tool = str(part.get("tool") or "tool")
        state = part.get("state")
        state_dict = state if isinstance(state, dict) else {}
        input_obj = state_dict.get("input")
        inp = input_obj if isinstance(input_obj, dict) else {}
        path = inp.get("filePath") or inp.get("path")
        pattern = inp.get("pattern")
        command = inp.get("command")
        if isinstance(command, str):
            arg = command
        elif isinstance(pattern, str) and isinstance(path, str):
            arg = f"{pattern} in {path}"
        elif isinstance(path, str):
            arg = path
        elif isinstance(pattern, str):
            arg = pattern
        else:
            arg = ""

        out: list[PrettyLine] = [("tool", f"{tool} {arg}".strip())]
        error = state_dict.get("error")
        if isinstance(error, str) and error.strip():
            detail = _opencode2_output(error, tail=True)
            if detail:
                out.append(("meta", f"error: {detail}"))
            return out

        output = state_dict.get("output")
        if isinstance(output, str) and output.strip():
            # Test/build summaries tend to be at the end of shell output;
            # file/search previews are more useful from the beginning.
            detail = _opencode2_output(output, tail=tool in {"bash", "shell"})
            if detail:
                out.append(("meta", f"↳ {detail}"))
        return out
    if kind == "step_finish":
        reason = part.get("reason")
        # OpenCode finishes a step before every tool call. It is an internal
        # turn boundary, not completion of the mutation or iteration.
        if reason == "tool-calls":
            return []
        tokens = part.get("tokens")
        tok = 0
        if isinstance(tokens, dict):
            output = tokens.get("output", 0)
            reasoning = tokens.get("reasoning", 0)
            if isinstance(output, (int, float)):
                tok += int(output)
            if isinstance(reasoning, (int, float)):
                tok += int(reasoning)
        parts = ["done"]
        if isinstance(reason, str) and reason:
            parts.append(reason)
        if tok:
            parts.append(f"{int(tok):,} tok")
        return [("result", " · ".join(parts))]
    return []


def _opencode2_output(text: str, *, tail: bool, max_lines: int = 6,
                      max_chars: int = 900) -> str:
    """Keep tool feedback useful without dumping whole files into the log."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    omitted = max(0, len(lines) - max_lines)
    if tail:
        shown = lines[-max_lines:]
        if omitted:
            shown.insert(0, f"… ({omitted} earlier lines)")
    else:
        shown = lines[:max_lines]
        if omitted:
            shown.append(f"… ({omitted} more lines)")
    compact = "\n".join(shown)
    if len(compact) > max_chars:
        if tail:
            compact = "…" + compact[-(max_chars - 1):].lstrip()
        else:
            compact = compact[:max_chars - 1].rstrip() + "…"
    return compact


def _brief(obj: dict) -> list[PrettyLine]:
    """The mutation brief the loop fed this iteration -- emitted once, up front,
    as a synthetic ``{"type": "nethackers_brief", "text": ...}`` event so it
    heads the agent log (backend-agnostic; see harness/loop.py)."""
    text = obj.get("text")
    if isinstance(text, str) and text.strip():
        return [("brief", "── brief ──\n" + text.strip())]
    return []


def prettify(backend: str, line: str) -> list[PrettyLine]:
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return []
        # backend-agnostic: the loop's synthetic brief event heads each iter log.
        if obj.get("type") == "nethackers_brief":
            return _brief(obj)
        if backend == "claude":
            return _claude(obj)
        if backend == "codex":
            return _codex(obj)
        if backend == "opencode2":
            return _opencode2(obj)
    except Exception:
        return []
    return []
