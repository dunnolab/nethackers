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

import base64
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

Run = Callable[..., "subprocess.CompletedProcess[str]"]

# `git push` logs in with gh, like every gh call here. Left alone it would use
# the machine's own git credential setup, which may have nothing for
# github.com -- git then asks for a username on the terminal, and an unattended
# evolve run waits on that prompt for hours. The empty helper drops every other
# helper first.
_GH_LOGIN = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


class PublishError(Exception):
    """A `gh`/git step failed, or a precondition was not met."""


def _run(run: Run, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Publishing runs unattended, so a missing login must fail the step, never
    # prompt: GIT_TERMINAL_PROMPT=0 also reaches the git that `gh repo clone` runs.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return run(cmd, check=check, capture_output=True, text=True, env=env)
    except FileNotFoundError as e:  # gh/git not installed
        raise PublishError(f"{cmd[0]} is not installed") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise PublishError(f"`{' '.join(cmd[:3])}` failed: {detail}") from e


def gh_login(run: Run = subprocess.run) -> str | None:
    """The GitHub login `gh` is authenticated as, or ``None`` if `gh` is
    missing or not logged in.

    Never raises for the not-installed / not-authed case -- the caller turns
    ``None`` into a friendly "set up gh" message rather than a crash. A hung
    ``gh`` (a slow network) is treated as not logged in after 10 seconds, the
    subprocess convention elsewhere in this codebase.
    """
    try:
        proc = run(["gh", "api", "user", "-q", ".login"], check=True,
                   capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    login = proc.stdout.strip()
    return login or None


def gh_state(*, run: Run = subprocess.run, which=shutil.which) -> tuple[str | None, str]:
    """(login, state) where state is 'authed' / 'missing' / 'unauthed' -- so a
    caller can tell "install gh" from "run gh auth login" (different fixes;
    conflating them is a top onboarding confusion). Never raises. See spec §5.6."""
    if which("gh") is None:
        return None, "missing"
    login = gh_login(run=run)
    return (login, "authed") if login is not None else (None, "unauthed")


# The public site, not the configured hub: this copy lands on a real GitHub repo,
# so it must never point at a dev/local hub.
_SITE = "https://nethackers.dunnolab.ai"
_DESCRIPTION = "helping to solve nethack @ nethackers.dunnolab.ai"
_TOPICS = "nethack,nethackers"
# Static on purpose -- no scores, versions or objective names -- so a README
# written once never goes stale.
_README = """\
# {name}

Storage for the NetHack bots I've submitted to [NetHackers]({site}),
an open effort to solve NetHack.

- **My results:** {site}/h/{owner}
- **Where's the code?** Each run lives on its own branch; the leaderboard pins
  every bot to an exact commit. Fetch one:
  `nethackers pull github.com/{owner}/{name}@<commit> ./bot`
- **Want to help?** `pip install nethackers`

<sub>Created by the `nethackers` CLI. It's your repo — edit or delete this file freely.</sub>
"""


def _dress(slug: str, *, homepage: str, run: Run) -> None:
    """Give the repo its public face: a README on the default branch, then the
    About fields (description, website = the owner's page on the site, topics).
    Only ever fills what is empty -- it is the owner's repo: a README the
    default branch already renders is kept, as is a website (``homepage``, the
    current value) the owner set; topics are additive.

    The README goes through the contents API rather than a clone: on a
    just-created empty repo that PUT *is* the first commit, so the default
    branch becomes a README-only landing page before any bot branch exists
    (pushed first, a run's branch would become the default instead).

    Best-effort and never raises: this is cosmetics, and a failure here must
    not fail a publish. The description is written LAST because it doubles as
    the "dressed" marker (``ensure_repo`` only dresses a repo whose description
    is empty): a step that fails leaves it unset, so the next publish retries."""
    owner, _, name = slug.partition("/")
    readme = _README.format(name=name, owner=owner, site=_SITE)
    about = ["gh", "repo", "edit", slug, "--description", _DESCRIPTION]
    if not homepage:
        about += ["--homepage", f"{_SITE}/h/{owner}"]
    about += ["--add-topic", _TOPICS]
    try:
        # GET /readme resolves the README GitHub would render (any name/case);
        # nonzero = none yet (or an empty repo).
        if _run(run, ["gh", "api", f"repos/{slug}/readme", "--silent"],
                check=False).returncode != 0:
            _run(run, ["gh", "api", "--method", "PUT", f"repos/{slug}/contents/README.md",
                       "-f", "message=nethackers: add README",
                       "-f", f"content={base64.b64encode(readme.encode()).decode()}"])
        _run(run, about)
    except PublishError:
        pass


def ensure_repo(slug: str, *, run: Run = subprocess.run) -> None:
    """Ensure the repo ``slug`` (``owner/name``) exists AND is public: create it
    public if absent, and flip it public if a pre-existing repo is private.

    Solutions must be publicly fetchable -- the hub validates commit-existence
    with an identity-scoped token that 404s on a private repo, which surfaces as
    ``MissingCommit`` (a 400 that silently drops every win). One ``gh repo view
    --json`` probes; a non-zero exit means "absent". A create/edit failure
    raises ``PublishError``.

    A repo it just created, or one whose description is still empty, also gets
    its public face (``_dress``) -- best-effort, never a reason to fail. A repo
    with a description costs no extra call: the same probe carries it."""
    try:
        proc = run(["gh", "repo", "view", slug, "--json", "visibility,description,homepageUrl"],
                   check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise PublishError("gh is not installed") from e
    except subprocess.CalledProcessError:
        _run(run, ["gh", "repo", "create", slug, "--public"])  # absent -> create public
        _dress(slug, homepage="", run=run)
        return
    try:
        info = json.loads(proc.stdout or "{}")
    except ValueError:
        info = {}
    if not isinstance(info, dict):
        info = {}
    visibility = str(info.get("visibility", "")).lower()
    if visibility != "public":  # pre-existing private/internal repo -> make it fetchable
        _run(run, ["gh", "repo", "edit", slug, "--visibility", "public",
                   "--accept-visibility-change-consequences"])
    # Dress only a repo we can SEE is undressed: the description must be present
    # in the probe and empty. An unreadable probe proves nothing, and filling
    # the description then could overwrite the owner's own words.
    if "description" in info and not info["description"]:
        _dress(slug, homepage=str(info.get("homepageUrl") or ""), run=run)


# Build/cache artifacts that pile up in the worktree from running the bot during
# eval/mutation -- never publish them into a solution repo.
_JUNK_NAMES = frozenset(
    {"__pycache__", ".DS_Store", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_JUNK_SUFFIXES = (".pyc", ".pyo", ".nbc", ".nbi")
_junk_ignore = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "*.nbc", "*.nbi",
    ".DS_Store", ".pytest_cache", ".mypy_cache", ".ruff_cache",
)


def _is_junk(name: str) -> bool:
    return name in _JUNK_NAMES or name.endswith(_JUNK_SUFFIXES)


def _sync_tree(src: Path, repo: Path) -> None:
    """Make ``repo``'s working tree match ``src``'s files, leaving ``.git``
    alone: delete everything tracked-or-not except ``.git``, then copy every
    top-level entry of ``src`` in -- skipping ``.git`` and build/cache artifacts
    (``__pycache__``, ``*.pyc``, numba ``*.nbc``/``*.nbi``, ...) at every level.
    ``git add -A`` afterwards turns this into the right adds/mods/deletes, so a
    re-publish also PRUNES any junk earlier leaked into the repo."""
    for item in repo.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in src.iterdir():
        if item.name == ".git" or _is_junk(item.name):
            continue
        dest = repo / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=_junk_ignore)
        else:
            shutil.copy2(item, dest)


def publish_solution(
    solution_dir: str | Path,
    slug: str,
    *,
    message: str,
    run: Run = subprocess.run,
    workdir: str | Path | None = None,
    ref: str | None = None,
) -> str:
    """Publish ``solution_dir`` as the content of ``slug`` and return the
    pushed commit sha.

    Clones ``slug`` (via `gh`, so it works even for a freshly-created empty
    repo), replaces its tracked content with ``solution_dir``'s files, commits
    ``message`` and pushes. Re-submitting identical content is a safe no-op:
    the commit reports "nothing to commit" and the current HEAD sha is
    returned unchanged. Requires ``solution_dir/nethackers.solution.json``.

    When ``ref`` is given, publishes to that branch instead of the repo's
    default branch: if ``origin/<ref>`` already exists (a prior publish under
    the same ref), the branch is rebuilt on that remote tip so re-publishing
    identical content still diffs to "nothing to commit" (idempotent); if not,
    it's created fresh off the just-cloned default branch. This is what lets
    concurrent callers (e.g. parallel evolve runs, each with their own ref)
    publish without racing on a single default-branch fast-forward.
    """
    solution_dir = Path(solution_dir)
    if not (solution_dir / "nethackers.solution.json").is_file():
        raise PublishError(f"{solution_dir}/nethackers.solution.json is missing")
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="nethackers-publish-"))
    repo = base / "repo"

    _run(run, ["gh", "repo", "clone", slug, str(repo)])
    if ref is not None:
        # check=False: a real `git rev-parse --verify` on a missing ref just
        # returns nonzero rather than raising. The `except` is only for the
        # test double, which (like this module's other fakes, e.g. `gh repo
        # view`) raises CalledProcessError to script a failing probe.
        try:
            probe = run(["git", "-C", str(repo), "rev-parse", "--verify", f"origin/{ref}"],
                        check=False, capture_output=True, text=True)
            ref_exists = probe.returncode == 0
        except subprocess.CalledProcessError:
            ref_exists = False
        if ref_exists:
            _run(run, ["git", "-C", str(repo), "checkout", "-B", ref, f"origin/{ref}"])
        else:
            _run(run, ["git", "-C", str(repo), "checkout", "-B", ref])
    _sync_tree(solution_dir, repo)
    _run(run, ["git", "-C", str(repo), "add", "-A"])

    committed = _run(run, ["git", "-C", str(repo), "commit", "-m", message], check=False)
    if committed.returncode != 0:
        blob = (committed.stdout or "") + (committed.stderr or "")
        if "nothing to commit" not in blob:
            raise PublishError(f"git commit failed: {blob.strip()}")
    else:
        target = f"HEAD:{ref}" if ref is not None else "HEAD"
        _run(run, ["git", "-C", str(repo), *_GH_LOGIN, "push", "-u", "origin", target])

    sha = _run(run, ["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    if not sha:
        raise PublishError("could not resolve the pushed commit sha")
    return sha
