"""Fetch a solution repository pinned to an exact commit for local
evaluation.

Requiring an explicit ``@<commit>`` (never a bare branch/tag) keeps the
evaluated solution reproducible: the caller always gets the exact tree that
was pinned, not whatever a branch currently happens to point to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from nethackers.github_ref import normalize_github_ref


def pull(repo_at_commit: str, dest: str | Path, *, runner=subprocess.run) -> Path:
    """Clone the commit pinned by ``repo_at_commit`` into ``dest``.

    ``repo_at_commit`` is ``"owner/name@<sha>"`` or a full
    ``"https://.../name@<sha>"`` URL -- the text after the last ``@`` is
    always the commit. Raises ``ValueError`` if no ``@<commit>`` suffix is
    present, and ``NonGitHubRef`` (a ``ValueError``) if what's left doesn't
    resolve to an unambiguous ``github.com/<owner>/<name>``. Registration is
    the primary gate against non-GitHub repos; this check is defense in
    depth so a client can never be pointed at an arbitrary git host (INV6).

    The clone and checkout are hardened against a hostile repo tree
    (threat 2): only the https protocol is allowed, symlink checkout is
    disabled, submodules are neither declared-recursed nor
    forced-recursed, and tags are not fetched.

    ``runner`` defaults to ``subprocess.run`` but is injectable so tests
    supply a fake that never shells out to a real ``git``.
    """
    # str.rpartition puts the *whole* input string in the third slot when the
    # separator isn't found at all (e.g. "a/b".rpartition("@") ==
    # ("", "", "a/b")), so checking `sha` alone would never fire for a
    # missing "@". Checking `sep` (empty only when "@" is absent) is what
    # actually detects a missing commit; `sha` is checked too so a trailing
    # "owner/name@" (empty commit) is also rejected. This parse happens
    # before normalization so a missing commit is always a plain ValueError,
    # never masked by a host-validation error.
    ref, sep, sha = repo_at_commit.rpartition("@")
    if not sep or not sha:
        raise ValueError("expected 'repo@commit'")
    # Accepts "owner/name", host-qualified "github.com/owner/name", or a full
    # https URL, and raises NonGitHubRef on anything else (scp form, other
    # schemes, other hosts, host obfuscation) -- see nethackers.github_ref.
    canonical = normalize_github_ref(ref)
    url = f"https://{canonical}.git"
    dest = Path(dest)
    runner(
        [
            "git",
            "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always",
            "-c", "core.symlinks=false",
            "-c", "fetch.recurseSubmodules=false",
            "clone", "--quiet", "--no-tags",
            url, str(dest),
        ],
        check=True,
    )
    runner(
        ["git", "-C", str(dest), "-c", "core.symlinks=false", "checkout", "--quiet", sha],
        check=True,
    )
    return dest
