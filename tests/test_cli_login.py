# tests/test_cli_login.py
"""Exercises ``nethackers.cli``'s ``login``/``logout``/``whoami`` verbs and
``evolve``'s credential-defaulted ``--owner``/``--token``, with
``device_login``/``whoami_from_token``/``_load_creds`` and
``credentials.save``/``clear`` all monkeypatched on their respective modules
-- no real GitHub device flow, network call, or write to
``~/.nethackers/credentials.json`` ever happens in this test suite.
"""
import json

import pytest

from nethackers import cli
from nethackers.config import Stage
from nethackers.harness import launch
from nethackers.hubclient import credentials as cred


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    return seed


# --- login -------------------------------------------------------------


def test_login_saves_resolved_identity(monkeypatch, capsys):
    saved = {}
    monkeypatch.setattr(
        cli, "device_login",
        lambda **_k: {"access_token": "gho_x", "refresh_token": "ghr_x", "expires_in": 28800},
    )
    monkeypatch.setattr(cli, "whoami_from_token", lambda tok, **_k: "castiel")
    monkeypatch.setattr(
        cred, "save", lambda c: saved.update(login=c.login, access_token=c.access_token)
    )

    assert cli.main(["login"]) == 0

    # the resolved (login, access_token) pair actually gets persisted -- not just "save was called"
    assert saved == {"login": "castiel", "access_token": "gho_x"}
    assert "castiel" in capsys.readouterr().err


def test_login_prompt_emits_clickable_hyperlink(monkeypatch):
    """The verification URL is emitted as an OSC 8 terminal hyperlink -- so a
    click opens the browser -- not merely styled text the terminal might fail to
    auto-detect (it does, inside the bordered panel)."""
    import io

    from rich.console import Console

    buf = io.StringIO()
    monkeypatch.setattr(cli, "err", Console(file=buf, force_terminal=True, width=100))
    monkeypatch.setattr(cli.clipboard, "copy", lambda _s: False)  # don't touch the real clipboard

    url = "https://github.com/login/device"
    cli._login_prompt(url, "WDJB-MJHT")
    out = buf.getvalue()

    assert "\x1b]8;" in out              # an OSC 8 hyperlink is emitted at all
    assert f";{url}\x1b\\" in out        # ...and its target is the verification URL


def test_login_passes_device_login_token_through_to_whoami_and_save(monkeypatch):
    # Strengthens the above: prove the *specific* access token device_login() returns is what
    # whoami_from_token() gets asked about, and (with the refresh token) what ends up saved --
    # not a stale/hardcoded one.
    seen = {}
    monkeypatch.setattr(
        cli, "device_login",
        lambda **_k: {
            "access_token": "tok-xyz-987", "refresh_token": "ghr-xyz", "expires_in": 28800,
        },
    )

    def fake_whoami(tok, **_k):
        seen["token_seen_by_whoami"] = tok
        return "someone-else"

    monkeypatch.setattr(cli, "whoami_from_token", fake_whoami)
    saved = {}
    monkeypatch.setattr(
        cred, "save",
        lambda c: saved.update(
            login=c.login, access_token=c.access_token, refresh_token=c.refresh_token
        ),
    )

    assert cli.main(["login"]) == 0
    assert seen["token_seen_by_whoami"] == "tok-xyz-987"
    assert saved == {
        "login": "someone-else", "access_token": "tok-xyz-987", "refresh_token": "ghr-xyz",
    }


def test_login_saves_full_credential(monkeypatch, tmp_path):
    from nethackers import cli
    from nethackers.hubclient import credentials as c
    monkeypatch.setattr(c, "path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(
        cli, "device_login",
        lambda **_k: {"access_token": "ghu_x", "refresh_token": "ghr_y", "expires_in": 28800},
    )
    monkeypatch.setattr(cli, "whoami_from_token", lambda tok: "sam")
    monkeypatch.setattr(cli, "_time_now", lambda: 1000.0)
    assert cli.main(["login"]) == 0
    creds = c.load()
    assert (
        creds.login == "sam"
        and creds.access_token == "ghu_x"
        and creds.expires_at == 1000.0 + 28800
    )


def test_authed_token_refreshes_when_expired(monkeypatch, tmp_path):
    from nethackers import cli
    from nethackers.hubclient import credentials as c
    monkeypatch.setattr(c, "path", lambda: tmp_path / "credentials.json")
    c.save(c.Credentials("sam", "ghu_old", "ghr_y", expires_at=500.0))
    monkeypatch.setattr(cli, "_time_now", lambda: 1000.0)
    monkeypatch.setattr(
        cli, "refresh_access_token",
        lambda rt, **k: {"access_token": "ghu_new", "refresh_token": "ghr_z", "expires_in": 28800},
    )
    assert cli._authed_token() == "ghu_new"
    assert c.load().access_token == "ghu_new"


def test_authed_token_raises_autherror_when_refresh_fails(monkeypatch, tmp_path):
    # _authed_token() now delegates to the same TokenSource adapter
    # harness.launch wires into evolve -- a dead refresh token must surface
    # as AuthError (caught by main()'s top-level guard), not a raw
    # DeviceFlowError/httpx exception leaking out of the CLI.
    from nethackers import cli
    from nethackers.hubclient import credentials as c
    from nethackers.hubclient.auth import AuthError
    from nethackers.hubclient.register import DeviceFlowError
    monkeypatch.setattr(c, "path", lambda: tmp_path / "credentials.json")
    c.save(c.Credentials("sam", "ghu_old", "ghr_y", expires_at=500.0))
    monkeypatch.setattr(cli, "_time_now", lambda: 1000.0)

    def dead_refresh(rt, **k):
        raise DeviceFlowError("bad_refresh_token")
    monkeypatch.setattr(cli, "refresh_access_token", dead_refresh)

    with pytest.raises(AuthError, match="run `nethackers login`"):
        cli._authed_token()
    assert c.load().access_token == "ghu_old"  # untouched -- nothing persisted on failure


# --- whoami --------------------------------------------------------------


def test_whoami_reports_and_exits_nonzero_when_absent(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: None)

    assert cli.main(["whoami"]) == 1
    assert "not logged in" in capsys.readouterr().err

    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "t"))
    assert cli.main(["whoami"]) == 0


def test_whoami_prints_login_to_stderr_by_default(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "sekrit-tok"))

    assert cli.main(["whoami"]) == 0

    captured = capsys.readouterr()
    assert "castiel" in captured.err
    assert captured.out == ""  # nothing on stdout unless -o json is asked for
    assert "sekrit-tok" not in captured.err  # the token itself never gets printed


def test_whoami_respects_o_json(monkeypatch, capsys):
    # No stage-related env/file is set up here -- the resolved stage is
    # whatever `load_stage()` naturally discovers (prod defaults, absent a
    # real .env.stack above this repo checkout); the "stage"/"hub" fields
    # are asserted against Stage()'s own defaults rather than hardcoded so
    # this doesn't silently drift from config.py's actual values.
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "sekrit-tok"))

    assert cli.main(["whoami", "-o", "json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "login": "castiel",
        "authenticated": True,
        "stage": Stage().name,
        "hub": Stage().hub_url,
    }
    assert "sekrit-tok" not in captured.out  # token never leaks into the JSON payload either


# --- logout ----------------------------------------------------------------


def test_logout_clears(monkeypatch, capsys):
    cleared = []
    monkeypatch.setattr(cred, "clear", lambda: cleared.append(True))

    assert cli.main(["logout"]) == 0

    assert cleared == [True]
    assert "logged out" in capsys.readouterr().err


# --- evolve's credential-defaulted --owner/--token ------------------------


def test_evolve_defaults_owner_token_to_stored_creds_when_flags_absent(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    # operator auth is a host precondition, not what these cred/flag tests cover;
    # without this they fail on any host (e.g. CI) with no claude/codex login.
    monkeypatch.setattr(cli, "sandbox_preflight", lambda operator: None)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "stored-tok"))

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    assert captured["owner"] == "castiel"
    assert captured["token"] == "stored-tok"


def test_evolve_falls_back_to_dev_when_no_creds_and_no_flags(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    # operator auth is a host precondition, not what these cred/flag tests cover;
    # without this they fail on any host (e.g. CI) with no claude/codex login.
    monkeypatch.setattr(cli, "sandbox_preflight", lambda operator: None)
    monkeypatch.setattr(cli, "_load_creds", lambda: None)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    assert captured["owner"] == "dev"
    assert captured["token"] == "dev-token"


def test_evolve_explicit_flags_win_over_stored_creds(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    # operator auth is a host precondition, not what these cred/flag tests cover;
    # without this they fail on any host (e.g. CI) with no claude/codex login.
    monkeypatch.setattr(cli, "sandbox_preflight", lambda operator: None)
    # Stored creds are present, but explicit flags must still win.
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "stored-tok"))

    rc = cli._run([
        "evolve", "val-dwa-law-fem", "--seed", str(seed), "--workdir", str(tmp_path / "w"),
        "--owner", "explicit-owner", "--token", "explicit-token",
    ])

    assert rc == 0
    assert captured["owner"] == "explicit-owner"
    assert captured["token"] == "explicit-token"
