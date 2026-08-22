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
    except Exception:
        return []
    return []
