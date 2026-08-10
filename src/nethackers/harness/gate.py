"""Cheap reject of broken mutants before spending real eval budget."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import _solution_digest
from nethackers.harness.evaluate import evaluate


def passes_gate(
    tree: str | Path,
    parent_digest: str,
    *,
    smoke_spec: ObjectiveSpec,
    image: str,
    now: str,
    runner=subprocess.run,
) -> tuple[bool, str]:
    tree = Path(tree)
    manifest_path = tree / "nethackers.solution.json"
    if not manifest_path.is_file():
        return False, "missing nethackers.solution.json manifest"
    manifest = json.loads(manifest_path.read_text())
    entrypoint = manifest.get("entrypoint")
    if not entrypoint or not (tree / entrypoint).is_file():
        return False, f"entrypoint file missing: {entrypoint!r}"
    if _solution_digest(tree) == parent_digest:
        return False, "child identical to parent"
    _, evidence = evaluate(tree, smoke_spec, image, now=now, runner=runner)
    if not evidence.results or evidence.results[0].status != "completed":
        return False, "smoke episode did not complete"
    return True, "ok"
