"""Pure conversion of a coding-agent stream line into display lines for the
Mutation-logs tab. Never raises: any unparseable / unknown shape yields []
(the same defensive discipline as operator._usage_tokens)."""
from __future__ import annotations

import json

PrettyLine = tuple[str, str]  # (kind, text); kind in {assistant, tool, result, meta}

_CLAUDE_VERB = {"Edit": "edit", "MultiEdit": "edit", "Write": "write",
                "Read": "read", "Bash": "bash"}


def _out_tokens(usage: object) -> int:
    """Defensive output-token read for the result line (tolerates any shape)."""
    if not isinstance(usage, dict):
        return 0
    value = usage.get("output_tokens")
    return int(value) if isinstance(value, (int, float)) else 0


def _truncate(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


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
                out.append(("tool", _truncate(f"{verb} {arg}".strip())))
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


def _codex(obj: dict) -> list[PrettyLine]:
    # Best-effort; codex's exact event keys are refined during manual
    # acceptance. Recognized shapes only, else []. Never raises.
    text = obj.get("text") or obj.get("message")
    if isinstance(text, str) and text.strip():
        return [("assistant", text.strip())]
    cmd = obj.get("command")
    if isinstance(cmd, str) and cmd.strip():
        return [("tool", _truncate(f"bash {cmd.strip()}"))]
    return []


def prettify(backend: str, line: str) -> list[PrettyLine]:
    try:
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return []
        if backend == "claude":
            return _claude(obj)
        if backend == "codex":
            return _codex(obj)
    except Exception:
        return []
    return []
