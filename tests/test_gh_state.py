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
