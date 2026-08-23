import subprocess
from pathlib import Path

import pytest

from nethackers.hubclient import publish as P


def _cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Scripts the gh/git commands `publish` shells out to, recording every
    call. `gh repo clone` materialises an (empty, or stale-seeded) repo dir so
    the real `_sync_tree` runs against a real filesystem."""

    def __init__(self, *, repo_exists=True, visibility="public", commit_rc=0,
                 sha="a" * 40, seed_old=True):
        self.calls: list[list[str]] = []
        self.repo_exists = repo_exists
        self.visibility = visibility
        self.commit_rc = commit_rc
        self.sha = sha
        self.seed_old = seed_old

    def __call__(self, cmd, *, check=False, capture_output=False, text=False):
        self.calls.append(cmd)
        head = cmd[:3]
        if head == ["gh", "api", "user"]:
            return _cp(cmd, 0, stdout="sam\n")
        if head == ["gh", "repo", "view"]:
            if self.repo_exists:
                import json
                return _cp(cmd, 0, stdout=json.dumps({"visibility": self.visibility}))
            raise subprocess.CalledProcessError(1, cmd, stderr="Not Found")
        if head == ["gh", "repo", "create"]:
            return _cp(cmd, 0)
        if head == ["gh", "repo", "edit"]:
            return _cp(cmd, 0)
        if head == ["gh", "repo", "clone"]:
            dest = Path(cmd[4])  # gh repo clone <slug> <dest>
            (dest / ".git").mkdir(parents=True)
            if self.seed_old:
                (dest / "old.txt").write_text("stale")
            return _cp(cmd, 0)
        if cmd[:2] == ["git", "-C"]:
            sub = cmd[3]
            if sub == "commit":
                if self.commit_rc != 0:
                    return _cp(cmd, self.commit_rc, stdout="nothing to commit, working tree clean")
                return _cp(cmd, 0)
            if sub == "rev-parse":
                return _cp(cmd, 0, stdout=self.sha + "\n")
            return _cp(cmd, 0)  # add / push
        return _cp(cmd, 0)


def _solution(tmp_path: Path) -> Path:
    sol = tmp_path / "sol"
    (sol / "pkg").mkdir(parents=True)
    (sol / "nethackers.solution.json").write_text('{"root": ".", "entrypoint": "bot.py"}')
    (sol / "bot.py").write_text("VERSION=1\n")
    (sol / "pkg" / "helper.py").write_text("x = 1\n")
    return sol


def test_gh_login_returns_login():
    assert P.gh_login(FakeRun()) == "sam"


def test_gh_login_none_when_gh_missing():
    def r(cmd, **k):
        raise FileNotFoundError
    assert P.gh_login(r) is None


def test_gh_login_none_when_unauthed():
    def r(cmd, **k):
        raise subprocess.CalledProcessError(1, cmd)
    assert P.gh_login(r) is None


def test_ensure_repo_creates_when_absent():
    fake = FakeRun(repo_exists=False)
    P.ensure_repo("sam/nethacker", run=fake)
    assert ["gh", "repo", "create", "sam/nethacker", "--public"] in fake.calls


def test_ensure_repo_noop_when_present_and_public():
    fake = FakeRun(repo_exists=True, visibility="public")
    P.ensure_repo("sam/nethacker", run=fake)
    assert not any(c[:3] == ["gh", "repo", "create"] for c in fake.calls)
    assert not any(c[:3] == ["gh", "repo", "edit"] for c in fake.calls)  # already public


def test_ensure_repo_makes_private_public():
    # Solutions must be publicly fetchable: the hub validates commit-existence
    # with an identity-scoped token that 404s on a private repo (-> MissingCommit,
    # a 400 that silently drops every win). A pre-existing private repo must be
    # flipped public, not left as-is.
    fake = FakeRun(repo_exists=True, visibility="private")
    P.ensure_repo("sam/nethacker", run=fake)
    assert ["gh", "repo", "edit", "sam/nethacker", "--visibility", "public",
            "--accept-visibility-change-consequences"] in fake.calls


def test_publish_solution_syncs_commits_and_returns_sha(tmp_path):
    sol = _solution(tmp_path)
    fake = FakeRun(sha="b" * 40, seed_old=True)
    sha = P.publish_solution(sol, "sam/nethacker", message="update", run=fake,
                             workdir=tmp_path / "wd")
    assert sha == "b" * 40
    repo = tmp_path / "wd" / "repo"
    assert (repo / "bot.py").read_text() == "VERSION=1\n"       # solution copied in
    assert (repo / "pkg" / "helper.py").exists()                # subdir copied
    assert not (repo / "old.txt").exists()                      # stale content removed
    assert (repo / ".git").exists()                             # .git preserved
    verbs = [c[3] for c in fake.calls if c[:2] == ["git", "-C"]]
    assert verbs == ["add", "commit", "push", "rev-parse"]


def test_publish_excludes_pycache_and_build_junk(tmp_path):
    # The worktree accumulates __pycache__ / *.pyc / numba *.nbc caches from
    # running the bot during eval; none of that belongs in a solution repo.
    sol = _solution(tmp_path)
    (sol / "__pycache__").mkdir()
    (sol / "__pycache__" / "bot.cpython-311.pyc").write_text("x")
    (sol / "pkg" / "__pycache__").mkdir()
    (sol / "pkg" / "__pycache__" / "helper.cpython-311.pyc").write_text("x")
    (sol / "pkg" / "__pycache__" / "utils.bfs.py311.1.nbc").write_text("x")
    (sol / "stray.pyc").write_text("x")
    (sol / ".DS_Store").write_text("x")

    P.publish_solution(sol, "sam/nethacker", message="m", run=FakeRun(sha="d" * 40),
                       workdir=tmp_path / "wd")
    repo = tmp_path / "wd" / "repo"

    assert (repo / "bot.py").exists() and (repo / "pkg" / "helper.py").exists()  # source kept
    assert not (repo / "__pycache__").exists()
    assert not (repo / "pkg" / "__pycache__").exists()
    assert not (repo / "stray.pyc").exists() and not (repo / ".DS_Store").exists()
    junk = [p for p in repo.rglob("*") if p.suffix in {".pyc", ".pyo", ".nbc", ".nbi"}
            or p.name == "__pycache__"]
    assert junk == []


def test_publish_solution_no_changes_skips_push(tmp_path):
    sol = _solution(tmp_path)
    fake = FakeRun(commit_rc=1, sha="c" * 40)  # "nothing to commit"
    sha = P.publish_solution(sol, "sam/nethacker", message="noop", run=fake,
                             workdir=tmp_path / "wd")
    assert sha == "c" * 40
    verbs = [c[3] for c in fake.calls if c[:2] == ["git", "-C"]]
    assert verbs == ["add", "commit", "rev-parse"]             # no push


def test_publish_solution_missing_manifest_raises(tmp_path):
    (tmp_path / "bare").mkdir()
    with pytest.raises(P.PublishError):
        P.publish_solution(tmp_path / "bare", "sam/nethacker", message="m",
                           run=FakeRun(), workdir=tmp_path / "wd")


def test_run_wraps_called_process_error():
    def r(cmd, **k):
        raise subprocess.CalledProcessError(1, cmd, stderr="boom")
    with pytest.raises(P.PublishError):
        P.ensure_repo("sam/nethacker", run=r)  # view raises -> create raises -> PublishError
