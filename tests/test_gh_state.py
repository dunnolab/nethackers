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


def test_gh_login_gives_up_on_a_hung_gh():
    import subprocess

    from nethackers.hubclient.publish import gh_login

    def hung(cmd, **kw):
        assert kw.get("timeout") == 10
        raise subprocess.TimeoutExpired(cmd, 10)

    assert gh_login(run=hung) is None
