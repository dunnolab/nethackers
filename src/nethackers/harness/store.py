"""Content-addressed local store of solution file-trees.

The hub stores only git ``{repo, commit}`` pointers, not files, so a
single-contributor local loop keeps its own trees here, keyed by the same
``_solution_digest`` the arena/register path uses.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nethackers.eval.runner import _solution_digest


class LocalTreeStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        return self._root / digest.split(":", 1)[1]

    def has(self, digest: str) -> bool:
        return self.path(digest).is_dir()

    def save(self, src: str | Path) -> str:
        src = Path(src)
        digest = _solution_digest(src)
        dest = self.path(digest)
        if not dest.is_dir():
            shutil.copytree(src, dest)
        return digest
