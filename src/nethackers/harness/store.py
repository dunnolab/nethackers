"""Content-addressed local store of solution file-trees.

The hub stores only git ``{repo, commit}`` pointers, not files, so a
single-contributor local loop keeps its own trees here, keyed by the same
``_solution_digest`` the arena/register path uses.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from nethackers.eval.runner import _solution_digest


def _key(digest: str) -> str:
    """One filesystem-safe directory segment for a solution digest.

    Two digest namespaces flow through the store: local **content** digests
    ``"sha256:<hex>"`` (from ``_solution_digest``) and atom **hub identities**
    ``"<host>/<owner>/<repo>@<commit>"`` (no colon). For a content digest the
    hex suffix is the historical, already-safe name; an atom identity is
    flattened (``/``, ``@`` -> ``_``) so it never nests directories or crashes
    ``split(":")[1]``.
    """
    body = digest.split(":", 1)[1] if ":" in digest else digest
    return re.sub(r"[^A-Za-z0-9._-]", "_", body)


class LocalTreeStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def path(self, digest: str) -> Path:
        return self._root / _key(digest)

    def has(self, digest: str) -> bool:
        return self.path(digest).is_dir()

    def save(self, src: str | Path) -> str:
        src = Path(src)
        digest = _solution_digest(src)
        self._publish(src, self.path(digest))
        return digest

    def save_as(self, digest: str, src: str | Path) -> None:
        """Cache ``src`` under an explicit ``digest`` key -- used for atom
        ``repo@commit`` identities, whose key is the git commit (not the tree's
        content hash), so ``save`` (which recomputes the content hash) can't
        produce it. Idempotent: a present tree is left as-is."""
        self._publish(Path(src), self.path(digest))

    def _publish(self, src: Path, dest: Path) -> None:
        """Copy ``src`` -> ``dest`` atomically. Content-addressed: if ``dest``
        already exists (or a concurrent writer wins the race) it is the same
        tree, so leave it. Readers never see a partially-copied ``dest``."""
        if dest.is_dir():
            return
        tmp = self._root / f".tmp-{uuid4().hex}"
        shutil.copytree(src, tmp)
        try:
            os.rename(tmp, dest)        # atomic publish (tmp is a sibling -> same filesystem)
        except OSError:                 # lost the race; dest now present, identical content
            shutil.rmtree(tmp, ignore_errors=True)
