# tests/test_cli_gh_readiness.py
"""A hub-logged-in-but-gh-not-ready contestant currently evolves, wins, and the
win silently stays local (PublishError only fires at push time, deep inside a
run). `evolve` now warns up front, at the moment of intent, using the same
three-state `gh_state()` check `submit` uses -- see cli.py's evolve-Start
block and `publish.gh_state`.

``cli.main``'s real evolve args: ``objective`` is POSITIONAL (not
``--objective``), and ``--no-tui`` is a TOP-LEVEL flag (must precede the
``evolve`` subcommand, not follow it -- argparse subparsers only see their own
+ parents' flags once dispatched). Both `sandbox_preflight` and
`image_present` are stubbed so this hermetic test never shells out to a real
container runtime (same pattern as test_cli_evolve.py's autouse fixture),
independent of whether *this* host happens to have docker/gh/claude set up.
"""
import contextlib

import nethackers.cli as cli


def _run_evolve(monkeypatch, capsys, gh_state_ret, owner="octocat"):
    # Stub gh_state + the heavy evolve machinery so we exercise only the warning.
    monkeypatch.setattr(cli, "gh_state", lambda: gh_state_ret)
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "prepare_evolve", lambda params: (_ for _ in ()).throw(SystemExit))
    monkeypatch.setattr(
        cli, "_load_creds",
        lambda: type("C", (), {"access_token": "t", "login": owner})(),
    )
    with contextlib.suppress(SystemExit):
        cli.main(["--no-tui", "evolve", "val-dwa-law-fem", "--seed", "roots/autoascend"])
    return capsys.readouterr().err


def test_evolve_warns_when_gh_unauthed(monkeypatch, capsys):
    err = _run_evolve(monkeypatch, capsys, (None, "unauthed"))
    assert "gh auth login" in err and "wins won't publish" in err


def test_evolve_warns_when_gh_missing(monkeypatch, capsys):
    err = _run_evolve(monkeypatch, capsys, (None, "missing"))
    assert "install the GitHub CLI" in err


def test_evolve_quiet_when_gh_authed(monkeypatch, capsys):
    err = _run_evolve(monkeypatch, capsys, ("octocat", "authed"))
    assert "wins won't publish" not in err
