import subprocess

from nethackers.hubclient.publish import gh_state


def _run_ok(argv, **kw):
    class P:
        stdout = "octocat\n"
    return P()

def _run_unauthed(argv, **kw):
    raise subprocess.CalledProcessError(1, argv)


def test_gh_state_missing():
    assert gh_state(which=lambda _: None, run=_run_ok) == (None, "missing")

def test_gh_state_unauthed():
    assert gh_state(which=lambda _: "/usr/bin/gh", run=_run_unauthed) == (None, "unauthed")

def test_gh_state_authed():
    assert gh_state(which=lambda _: "/usr/bin/gh", run=_run_ok) == ("octocat", "authed")


def _run_timeout(argv, **kw):
    raise subprocess.TimeoutExpired(argv, kw.get("timeout", 10))


def test_gh_state_unknown_when_gh_does_not_answer():
    # `gh api user` calls the GitHub API: a hang is neither "missing" nor
    # "not logged in", so it gets its own state instead of a wrong fix.
    assert gh_state(which=lambda _: "/usr/bin/gh", run=_run_timeout) == (None, "unknown")


def test_gh_login_passes_a_ten_second_timeout():
    seen: dict = {}

    def run(argv, **kw):
        seen.update(kw)

        class P:
            stdout = "octocat\n"
        return P()

    gh_state(which=lambda _: "/usr/bin/gh", run=run)
    assert seen["timeout"] == 10
