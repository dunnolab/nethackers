import os
import subprocess
from pathlib import Path

import pytest

from nethackers.hubclient import publish as P


def _cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _git_verb(cmd):
    """The git subcommand, past any global options (``-C <dir>``, ``-c <k=v>``)."""
    i = 1
    while cmd[i] in ("-C", "-c"):
        i += 2
    return cmd[i]


def _pushes(fake):
    return [c for c in fake.calls if c[0] == "git" and _git_verb(c) == "push"]


class FakeRun:
    """Scripts the gh/git commands `publish` shells out to, recording every
    call. `gh repo clone` materialises an (empty, or stale-seeded) repo dir so
    the real `_sync_tree` runs against a real filesystem."""

    def __init__(self, *, repo_exists=True, visibility="public", commit_rc=0,
                 sha="a" * 40, seed_old=True, description="my bots", homepage="",
                 has_readme=False, view_stdout=None, readme_put_fails=False,
                 about_fails=False):
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []   # the env= each call got, index-aligned with calls
        self.repo_exists = repo_exists
        self.visibility = visibility
        self.commit_rc = commit_rc
        self.sha = sha
        self.seed_old = seed_old
        self.description = description
        self.homepage = homepage
        self.has_readme = has_readme
        self.view_stdout = view_stdout
        self.readme_put_fails = readme_put_fails
        self.about_fails = about_fails

    def __call__(self, cmd, *, check=False, capture_output=False, text=False, env=None,
                 timeout=None):
        self.calls.append(cmd)
        self.envs.append(env)
        head = cmd[:3]
        if head == ["gh", "api", "user"]:
            return _cp(cmd, 0, stdout="sam\n")
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/readme"):
            # GET /repos/<slug>/readme -- probed with check=False, so an absent
            # README is a nonzero exit, not a raise (as real subprocess does).
            if self.has_readme:
                return _cp(cmd, 0)
            return _cp(cmd, 1, stderr="gh: Not Found (HTTP 404)")
        if cmd[:4] == ["gh", "api", "--method", "PUT"]:
            if self.readme_put_fails:
                raise subprocess.CalledProcessError(1, cmd, stderr="gh: Server Error (HTTP 502)")
            return _cp(cmd, 0)
        if head == ["gh", "repo", "view"]:
            if self.repo_exists:
                import json
                if self.view_stdout is not None:
                    return _cp(cmd, 0, stdout=self.view_stdout)
                # Like real gh: only the fields the caller asked for come back,
                # so a probe that stops requesting one loses it here too.
                asked = cmd[cmd.index("--json") + 1].split(",")
                full = {"visibility": self.visibility, "description": self.description,
                        "homepageUrl": self.homepage}
                return _cp(cmd, 0, stdout=json.dumps({k: full[k] for k in asked}))
            raise subprocess.CalledProcessError(1, cmd, stderr="Not Found")
        if head == ["gh", "repo", "create"]:
            return _cp(cmd, 0)
        if head == ["gh", "repo", "edit"]:
            if self.about_fails and "--description" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="gh: Server Error (HTTP 502)")
            return _cp(cmd, 0)
        if head == ["gh", "repo", "clone"]:
            dest = Path(cmd[4])  # gh repo clone <slug> <dest>
            (dest / ".git").mkdir(parents=True)
            if self.seed_old:
                (dest / "old.txt").write_text("stale")
            return _cp(cmd, 0)
        if cmd[0] == "git":
            sub = _git_verb(cmd)
            if sub == "commit":
                if self.commit_rc != 0:
                    return _cp(cmd, self.commit_rc, stdout="nothing to commit, working tree clean")
                return _cp(cmd, 0)
            if sub == "rev-parse" and "--verify" in cmd:
                raise subprocess.CalledProcessError(1, cmd, stderr="unknown revision")
            if sub == "rev-parse":
                return _cp(cmd, 0, stdout=self.sha + "\n")
            return _cp(cmd, 0)  # add / push / checkout
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


def _about_edits(fake):
    return [c for c in fake.calls if c[:3] == ["gh", "repo", "edit"] and "--description" in c]


def _readme_puts(fake):
    return [c for c in fake.calls if c[:4] == ["gh", "api", "--method", "PUT"]]


def test_new_repo_gets_its_about_fields():
    # The About box is the repo's one line of public copy: the description, a
    # clickable website (the hacker's own page) and the topics that list every
    # participant under github.com/topics/nethackers.
    fake = FakeRun(repo_exists=False)
    P.ensure_repo("sam/nethacker", run=fake)
    assert _about_edits(fake) == [[
        "gh", "repo", "edit", "sam/nethacker",
        "--description", "helping to solve nethack @ nethackers.dunnolab.ai",
        "--homepage", "https://nethackers.dunnolab.ai/h/sam",
        "--add-topic", "nethack,nethackers",
    ]]


def _put_readme_text(cmd):
    """The README body a contents-API PUT carries (its base64 `content` field)."""
    import base64
    (field,) = [tok for tok in cmd if tok.startswith("content=")]
    return base64.b64decode(field.removeprefix("content=")).decode()


def test_new_repo_gets_a_readme_pointing_at_its_owner():
    fake = FakeRun(repo_exists=False)
    P.ensure_repo("sam/nethacker", run=fake)
    (put,) = _readme_puts(fake)
    assert put[4] == "repos/sam/nethacker/contents/README.md"
    text = _put_readme_text(put)
    assert "https://nethackers.dunnolab.ai/h/sam" in text        # the owner's page
    assert "nethackers pull github.com/sam/nethacker@" in text   # how to fetch a bot


def test_readme_lands_before_the_about_fields():
    # The description doubles as the "dressed" marker (a set description is
    # never touched again), so it must be written LAST: written first, a README
    # step that then failed would never be retried.
    fake = FakeRun(repo_exists=False)
    P.ensure_repo("sam/nethacker", run=fake)
    (put,), (about,) = _readme_puts(fake), _about_edits(fake)
    assert fake.calls.index(put) < fake.calls.index(about)


@pytest.mark.parametrize("broken", ["readme_put_fails", "about_fails"])
def test_a_dressing_failure_never_blocks_the_publish(broken):
    # README + About are cosmetics. `ensure_repo` raising here would fail the
    # `submit`, or silently drop an evolve win (its publisher maps PublishError
    # to "keep the win local") -- over a missing README.
    fake = FakeRun(repo_exists=False, **{broken: True})
    P.ensure_repo("sam/nethacker", run=fake)  # must not raise


def test_a_failed_readme_leaves_the_description_unset():
    # ...so the next publish still sees an undressed repo and retries. Guards
    # against "hardening" _dress into independent best-effort steps, which
    # would write the marker over a README that never landed.
    fake = FakeRun(repo_exists=False, readme_put_fails=True)
    P.ensure_repo("sam/nethacker", run=fake)
    assert _about_edits(fake) == []


def test_existing_repo_with_an_empty_description_gets_dressed():
    # Repos created before this feature (or whose first dressing failed) are
    # picked up on their next publish.
    fake = FakeRun(repo_exists=True, description="")
    P.ensure_repo("sam/nethacker", run=fake)
    assert len(_readme_puts(fake)) == 1
    assert len(_about_edits(fake)) == 1


def test_a_repo_with_a_description_costs_no_extra_calls():
    # Steady state (dressed, or the owner wrote their own description): the one
    # probe `ensure_repo` always made, and nothing else -- no README lookup, no
    # edit. This runs on every evolve publish, so it must stay free.
    fake = FakeRun(repo_exists=True, description="sam's own words",
                   homepage="", has_readme=False)
    P.ensure_repo("sam/nethacker", run=fake)
    assert [c[:3] for c in fake.calls] == [["gh", "repo", "view"]]


def test_an_existing_readme_is_never_overwritten():
    # A default branch that already renders a README (a bot's own, any name or
    # case -- GitHub's /readme endpoint resolves them all) keeps it. The About
    # fields still land: a README that exists is a finished step, not a failure.
    fake = FakeRun(repo_exists=True, description="", has_readme=True)
    P.ensure_repo("sam/nethacker", run=fake)
    assert _readme_puts(fake) == []
    assert len(_about_edits(fake)) == 1


def test_an_owner_set_website_is_never_overwritten():
    fake = FakeRun(repo_exists=True, description="", homepage="https://sam.example")
    P.ensure_repo("sam/nethacker", run=fake)
    (about,) = _about_edits(fake)
    assert "--homepage" not in about
    assert "--description" in about  # the empty description is still filled


@pytest.mark.parametrize("stdout", ["not json", "[]", ""])
def test_an_unreadable_probe_never_dresses(stdout):
    # If we can't read the description we can't know the owner left it empty,
    # and filling it then could overwrite their words.
    fake = FakeRun(repo_exists=True, view_stdout=stdout)
    P.ensure_repo("sam/nethacker", run=fake)
    assert _readme_puts(fake) == [] and _about_edits(fake) == []


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
    verbs = [_git_verb(c) for c in fake.calls if c[0] == "git"]
    assert verbs == ["add", "commit", "push", "rev-parse"]


def test_publish_pushes_to_the_given_ref(tmp_path):
    sol = _solution(tmp_path)
    fake = FakeRun(sha="e" * 40)
    P.publish_solution(sol, "sam/nethacker", message="m", run=fake,
                       workdir=tmp_path / "wd", ref="evo-harness-v1/run-42")
    pushes = _pushes(fake)
    assert pushes, "expected a push"
    assert any("evo-harness-v1/run-42" in tok for c in pushes for tok in c)


def test_push_logs_in_through_gh(tmp_path):
    # Cloning a public repo needs no login, so the push is the first step that
    # does -- and a bare `git push` logs in with the machine's own git
    # credential setup, not gh's. Where that setup has nothing for github.com,
    # git asks for a username on the run's terminal and the evolve loop waits
    # on it for hours. The push must use the same gh login as every other step;
    # the empty helper first drops every helper git would otherwise try.
    fake = FakeRun()
    P.publish_solution(_solution(tmp_path), "sam/nethacker", message="m", run=fake,
                       workdir=tmp_path / "wd", ref="evo-harness-v1/run-1")
    [push] = _pushes(fake)
    i = push.index("credential.helper=")
    assert push[i - 1] == "-c"
    assert push[i + 1:i + 3] == ["-c", "credential.helper=!gh auth git-credential"]


def test_git_never_asks_for_a_login(tmp_path, monkeypatch):
    # gh's helper can still come up empty (gh logged out mid-run); git must
    # then fail at once, not prompt. Cleared first because a shell that already
    # exports it (Claude Code's does) would hide a missing one.
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    fake = FakeRun()
    P.publish_solution(_solution(tmp_path), "sam/nethacker", message="m", run=fake,
                       workdir=tmp_path / "wd")
    network = [env for cmd, env in zip(fake.calls, fake.envs, strict=True)
               if cmd[:3] == ["gh", "repo", "clone"] or cmd in _pushes(fake)]
    assert len(network) == 2   # the clone and the push
    for env in network:
        assert env is not None and env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["PATH"] == os.environ["PATH"]   # the rest of the env still flows through


def test_two_runs_push_distinct_refs(tmp_path):
    sol = _solution(tmp_path)
    for rid, sha in (("run-a", "a" * 40), ("run-b", "b" * 40)):
        fake = FakeRun(sha=sha)
        out = P.publish_solution(sol, "sam/nethacker", message="m", run=fake,
                                 workdir=tmp_path / f"wd-{rid}",
                                 ref=f"evo-harness-v1/{rid}")
        assert out == sha
        assert any(f"evo-harness-v1/{rid}" in tok for c in _pushes(fake) for tok in c)


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
    verbs = [_git_verb(c) for c in fake.calls if c[0] == "git"]
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
