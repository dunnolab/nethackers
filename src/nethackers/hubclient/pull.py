"""Fetch a solution repository pinned to an exact commit for local
evaluation.

Requiring an explicit ``@<commit>`` (never a bare branch/tag) keeps the
evaluated solution reproducible: the caller always gets the exact tree that
was pinned, not whatever a branch currently happens to point to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def pull(repo_at_commit: str, dest: str | Path, *, runner=subprocess.run) -> Path:
    """Clone the commit pinned by ``repo_at_commit`` into ``dest``.

    ``repo_at_commit`` is ``"owner/name@<sha>"`` or a full
    ``"https://.../name@<sha>"`` URL -- the text after the last ``@`` is
    always the commit. Raises ``ValueError`` if no ``@<commit>`` suffix is
    present.

    ``runner`` defaults to ``subprocess.run`` but is injectable so tests
    supply a fake that never shells out to a real ``git``.
    """
    # str.rpartition puts the *whole* input string in the third slot when the
    # separator isn't found at all (e.g. "a/b".rpartition("@") ==
    # ("", "", "a/b")), so checking `sha` alone would never fire for a
    # missing "@". Checking `sep` (empty only when "@" is absent) is what
    # actually detects a missing commit; `sha` is checked too so a trailing
    # "owner/name@" (empty commit) is also rejected.
    ref, sep, sha = repo_at_commit.rpartition("@")
    if not sep or not sha:
        raise ValueError("expected 'repo@commit'")
    url = ref if ref.startswith("http") else f"https://github.com/{ref}.git"
    dest = Path(dest)
    runner(["git", "clone", "--quiet", url, str(dest)], check=True)
    runner(["git", "-C", str(dest), "checkout", "--quiet", sha], check=True)
    return dest
