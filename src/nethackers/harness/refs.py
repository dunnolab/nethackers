"""Assemble the mutator's read-only `/refs/` reference tree: the
selector-chosen influence solutions, an island's recent rejected attempts,
the base's raw per-episode eval output, and a `CONTEXT.md` manifest tying
them together (design doc §3, "Sandbox provisioning"). This replaces
text-distilled feedback with real folders the agent reads and analyzes
itself -- the only thing written here is the manifest.

Pure filesystem I/O -- no hub, no network, no LLM -- so callers (the loop,
eventually `container_operator.py`) own everything about *which* folders to
pass in, and this module is asserted directly in tests.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nethackers.hubclient.publish import _junk_ignore

# (label, tree, note) -- `tree` is copied into dest/<section>/<label>/; `note`
# is a short caller-supplied line (score/outcome/hypothesis) rendered
# verbatim into CONTEXT.md so the agent knows what each folder is without
# having to infer it.
Ref = tuple[str, Path, str]


def _md_cell(text: str) -> str:
    # Keep a caller-supplied note from breaking the manifest's markdown
    # table if it happens to contain a pipe or newline (e.g. a multi-line
    # hypothesis comment pasted in verbatim).
    return text.replace("|", "\\|").replace("\n", " ")


def _copy_refs(dest_section: Path, refs: list[Ref]) -> None:
    for label, tree, _note in refs:
        shutil.copytree(tree, dest_section / label, ignore=_junk_ignore)


def _render_context(
    base_eval: str | None, influences: list[Ref], attempts: list[Ref],
    parent: Path | None = None,
) -> str:
    lines = ["# /refs/ manifest", "", "| section | label | note |", "| --- | --- | --- |"]
    for section, refs in (("influences", influences), ("attempts", attempts)):
        if not refs:
            lines.append(f"| {section} | — | (none yet) |")
        for label, _tree, note in refs:
            lines.append(f"| {section} | {_md_cell(label)} | {_md_cell(note)} |")
    if parent is not None:
        lines.append("")
        lines.append("Pristine copy of the bot you started from: `parent/` — "
                     "`diff -ru /refs/parent /workspace` shows your changes "
                     "(there is no git repo in `/workspace`).")
    if base_eval is not None:
        lines.append("")
        lines.append("Base's raw per-episode eval output: `parent-eval.json`.")
    return "\n".join(lines) + "\n"


def assemble(
    dest: Path,
    *,
    base_eval: str | None,
    influences: list[Ref],
    attempts: list[Ref],
    parent: Path | None = None,
) -> None:
    """Lay out `dest` as the mutator's `/refs/` tree: `dest/influences/<label>/`
    and `dest/attempts/<label>/` (each a junk-excluded copy of `tree`),
    `dest/parent/` (a junk-excluded copy of `parent`, only when given),
    `dest/parent-eval.json` (only when `base_eval` is given), and
    `dest/CONTEXT.md` (always -- a section/label/note manifest table).

    Filesystem-only: no hub, no network. Degrades gracefully -- empty
    `influences`/`attempts`, a `None` `parent`, and a `None` `base_eval` still
    produce a valid (mostly empty) tree, matching the "hub down / thin pool"
    fallback in design doc §7.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if influences:
        _copy_refs(dest / "influences", influences)
    if attempts:
        _copy_refs(dest / "attempts", attempts)
    if parent is not None:
        shutil.copytree(parent, dest / "parent", ignore=_junk_ignore)   # pristine start
    if base_eval is not None:
        (dest / "parent-eval.json").write_text(base_eval)
    (dest / "CONTEXT.md").write_text(
        _render_context(base_eval, influences, attempts, parent))
