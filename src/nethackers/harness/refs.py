"""Assemble the mutator's read-only `/refs/` tree: a pristine copy of the
current bot (`parent/`), its per-seed results (`parent-eval.json`), the recent
tried changes as real code trees (`attempts/<label>/` each with its own
`eval.json`), a per-identity scores table (`attempts.md`), and a `CONTEXT.md`
index. Pure filesystem I/O — no hub, no network, no loop internals rendered.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from nethackers.hubclient.publish import _junk_ignore

# Instruction-bearing files/dirs a pulled tree may carry: coding-agent config
# and rule files that a prompt-injection attack could plant to hijack the
# mutator's agent (spec §3d). Stripped -- not just hidden -- before the agent
# ever reads the tree; README.md and ordinary code are untouched.
# `opencode.json`/`opencode.jsonc`/`.opencode` block OpenCode config-injection
# STRUCTURALLY -- the tree never reaches the agent -- complementing the
# runtime `OPENCODE_DISABLE_PROJECT_CONFIG` flag (behavioral, and it has
# regressed upstream before; this strip holds even if that flag does).
_AGENT_CONFIG_NAMES = (
    "CLAUDE.md", "AGENTS.md", ".mcp.json", ".envrc",
    ".claude", ".codex", ".cursor", ".cursorrules", ".vscode",
    "opencode.json", "opencode.jsonc", ".opencode",
)
AGENT_CONFIG_IGNORE = shutil.ignore_patterns(*_AGENT_CONFIG_NAMES)


def _mutator_ignore(dir: str, names: list[str]) -> set[str]:
    """Combined ``shutil.copytree`` ignore for every copy that feeds the
    mutator's coding agent (worktree + /refs/): build junk (``_junk_ignore``)
    union agent-config/instruction files (``AGENT_CONFIG_IGNORE``). A single
    ``ignore=`` callable is all ``copytree`` accepts, so the two patterns are
    combined here rather than applied separately."""
    return set(_junk_ignore(dir, names)) | set(AGENT_CONFIG_IGNORE(dir, names))


@dataclass
class Attempt:
    label: str
    tree: Path
    hypothesis: str | None
    per_identity: dict[str, float] = field(default_factory=dict)
    overall: float = 0.0
    eval_json: str = ""


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _render_index() -> str:
    return (
        "# /refs/ — your reference material\n\n"
        "Everything here is read-only.\n\n"
        "- `parent/` — a pristine copy of the bot you're editing. `/workspace` "
        "started as a copy of this, so `diff -ru /refs/parent /workspace` shows "
        "exactly what you changed.\n"
        "- `parent-eval.json` — the current bot's result on every seed: one row "
        "per seed with its `trajectory_id`, `character` (identity), `progress` "
        "score, deepest `milestone`, and `cause_of_death`.\n"
        "- `attempts.md` — changes already tried, with the score each reached "
        "per identity.\n"
        "- `attempts/<n>/` — the code for each recent tried change, each with "
        "its own per-seed `eval.json`.\n"
    )


def _render_attempts(attempts: list[Attempt], identities: list[str]) -> str:
    if not attempts:
        return "# Changes tried\n\n_(none yet)_\n"
    header = "| # | change (its # hypothesis) | " + " | ".join(identities) + " | overall |"
    sep = "| -- | --- | " + " | ".join("---" for _ in identities) + " | --- |"
    rows = []
    for a in attempts:
        cells = " | ".join(
            f"{a.per_identity[i]:.3f}" if i in a.per_identity else "—"
            for i in identities)
        rows.append(f"| {a.label} | {_md_cell(a.hypothesis or '—')} | {cells} | {a.overall:.3f} |")
    return ("# Changes tried\n\n"
            "Each change that was tried, with the score it reached per identity. "
            "Code for the recent ones is under /refs/attempts/<n>/, each with "
            "its own per-seed eval.json.\n\n"
            + "\n".join([header, sep, *rows]) + "\n")


def assemble(dest: Path, *, parent: Path, parent_eval: str | None,
             attempts: list[Attempt], identities: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(parent, dest / "parent", ignore=_mutator_ignore)
    if parent_eval is not None:
        (dest / "parent-eval.json").write_text(parent_eval)
    for a in attempts:
        adir = dest / "attempts" / a.label
        shutil.copytree(a.tree, adir, ignore=_mutator_ignore)
        if a.eval_json:
            (adir / "eval.json").write_text(a.eval_json)
    (dest / "attempts.md").write_text(_render_attempts(attempts, identities))
    (dest / "CONTEXT.md").write_text(_render_index())
