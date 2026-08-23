"""Create/update the caller's public ``<owner>/nethacker`` repo via the GitHub
CLI (`gh`) + git, and return the pushed commit sha to register with the hub.

The *hub* never touches any of this -- publishing is a purely client-side act
done with the user's own `gh` credentials, so the hub stays read-only and
holds no write-capable token (design: `submit` keeps repo write on the client).

Every shell-out goes through an injected ``run`` (default ``subprocess.run``),
so ``tests/test_publish.py`` scripts the whole gh/git sequence without a real
`gh`, a real git, or the network. ``run`` is always called with
``capture_output=True, text=True``; failures are wrapped in ``PublishError``
with a clean message (never a raw traceback -- the CLI's top-level guard shows
it verbatim).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class PublishError(Exception):
    """A `gh`/git step failed, or a precondition was not met."""


def _run(run: Run, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return run(cmd, check=check, capture_output=True, text=True)
    except FileNotFoundError as e:  # gh/git not installed
        raise PublishError(f"{cmd[0]} is not installed") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise PublishError(f"`{' '.join(cmd[:3])}` failed: {detail}") from e


def gh_login(run: Run = subprocess.run) -> str | None:
    """The GitHub login `gh` is authenticated as, or ``None`` if `gh` is
    missing or not logged in.

    Never raises for the not-installed / not-authed case -- the caller turns
    ``None`` into a friendly "set up gh" message rather than a crash.
    """
    try:
        proc = run(["gh", "api", "user", "-q", ".login"], check=True,
                   capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    login = proc.stdout.strip()
    return login or None


def ensure_repo(slug: str, *, run: Run = subprocess.run) -> None:
    """Ensure the repo ``slug`` (``owner/name``) exists AND is public: create it
    public if absent, and flip it public if a pre-existing repo is private.

    Solutions must be publicly fetchable -- the hub validates commit-existence
    with an identity-scoped token that 404s on a private repo, which surfaces as
    ``MissingCommit`` (a 400 that silently drops every win). ``gh repo view
    --json visibility`` probes; a non-zero exit means "absent". A create/edit
    failure raises ``PublishError``."""
    try:
        proc = run(["gh", "repo", "view", slug, "--json", "visibility"],
                   check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise PublishError("gh is not installed") from e
    except subprocess.CalledProcessError:
        _run(run, ["gh", "repo", "create", slug, "--public"])  # absent -> create public
        return
    try:
        visibility = str(json.loads(proc.stdout or "{}").get("visibility", "")).lower()
    except (ValueError, TypeError):
        visibility = ""
    if visibility != "public":  # pre-existing private/internal repo -> make it fetchable
        _run(run, ["gh", "repo", "edit", slug, "--visibility", "public",
                   "--accept-visibility-change-consequences"])


def _sync_tree(src: Path, repo: Path) -> None:
    """Make ``repo``'s working tree match ``src``'s files, leaving ``.git``
    alone: delete everything tracked-or-not except ``.git``, then copy every
    top-level entry of ``src`` (skipping any ``.git``) in. ``git add -A``
    afterwards turns this into the right adds/mods/deletes."""
    for item in repo.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in src.iterdir():
        if item.name == ".git":
            continue
        dest = repo / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def publish_solution(
    solution_dir: str | Path,
    slug: str,
    *,
    message: str,
    run: Run = subprocess.run,
    workdir: str | Path | None = None,
) -> str:
    """Publish ``solution_dir`` as the content of ``slug`` and return the
    pushed commit sha.

    Clones ``slug`` (via `gh`, so it works even for a freshly-created empty
    repo), replaces its tracked content with ``solution_dir``'s files, commits
    ``message`` and pushes. Re-submitting identical content is a safe no-op:
    the commit reports "nothing to commit" and the current HEAD sha is
    returned unchanged. Requires ``solution_dir/nethackers.solution.json``.
    """
    solution_dir = Path(solution_dir)
    if not (solution_dir / "nethackers.solution.json").is_file():
        raise PublishError(f"{solution_dir}/nethackers.solution.json is missing")
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="nethackers-publish-"))
    repo = base / "repo"

    _run(run, ["gh", "repo", "clone", slug, str(repo)])
    _sync_tree(solution_dir, repo)
    _run(run, ["git", "-C", str(repo), "add", "-A"])

    committed = _run(run, ["git", "-C", str(repo), "commit", "-m", message], check=False)
    if committed.returncode != 0:
        blob = (committed.stdout or "") + (committed.stderr or "")
        if "nothing to commit" not in blob:
            raise PublishError(f"git commit failed: {blob.strip()}")
    else:
        _run(run, ["git", "-C", str(repo), "push", "-u", "origin", "HEAD"])

    sha = _run(run, ["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    if not sha:
        raise PublishError("could not resolve the pushed commit sha")
    return sha
