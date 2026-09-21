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


def test_login_github_unreachable_message_names_github_not_the_hub(monkeypatch, capsys):
    # issue #50: `login` talks to github.com, never the local hub. A
    # reachability failure there must NOT be blamed on the hub / "docker
    # compose up -d" (the blanket httpx handler's message) -- it must name
    # GitHub and point at the network/DNS.
    from nethackers.hubclient.register import GitHubUnreachable

    def _boom(**_k):
        raise GitHubUnreachable("https://github.com/login/device/code")

    monkeypatch.setattr(cli, "device_login", _boom)
    rc = cli.main(["login"])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "github" in err
    assert "network" in err or "dns" in err
    assert "cannot reach the hub" not in err
    assert "docker compose" not in err


_DEVICE_URL = "https://github.com/login/device"


def _plain(ansi):
    from rich.text import Text

    return Text.from_ansi(ansi).plain


class _Stdin:
    """Stand-in stdin whose ``readline`` (the user pressing Enter) records
    what was already on screen while the prompt waited."""

    def __init__(self, tty, screen, events):
        self._tty, self._screen, self._events = tty, screen, events

    def isatty(self):
        return self._tty

    def readline(self):
        self._events.append(("enter", _plain(self._screen.getvalue())))
        return "\n"


def _prompt(monkeypatch, *, stdin_tty=True, stderr_tty=True, can_open=True, opens=True):
    """Run ``cli._login_prompt`` against a fake terminal and browser; return
    the final screen text and the ordered Enter/open events."""
    import io

    from rich.console import Console

    screen = io.StringIO()
    events = []
    monkeypatch.setattr(cli, "err", Console(file=screen, force_terminal=stderr_tty, width=100))
    monkeypatch.setattr(cli.clipboard, "copy", lambda _s: True)  # never the real clipboard
    stdin = None if stdin_tty is None else _Stdin(stdin_tty, screen, events)
    monkeypatch.setattr(cli.sys, "stdin", stdin)  # None: fd 0 closed at startup
    monkeypatch.setattr(cli.browser, "can_open", lambda: can_open)
    monkeypatch.setattr(
        cli.browser, "open_url", lambda url: events.append(("open", url)) or opens
    )
    cli._login_prompt(_DEVICE_URL, "WDJB-MJHT")
    return _plain(screen.getvalue()), events


def test_login_prompt_shows_the_code_then_opens_the_browser_on_enter(monkeypatch):
    # gh-style: the code is on screen first, the prompt waits for Enter, and
    # only then does the browser open -- device_login polls after this returns.
    _, events = _prompt(monkeypatch)

    (step, on_screen), opened = events
    assert step == "enter" and opened == ("open", _DEVICE_URL)
    assert "WDJB-MJHT" in on_screen
    assert "Enter" in on_screen and _DEVICE_URL in on_screen


@pytest.mark.parametrize(
    ("stdin_tty", "stderr_tty", "can_open"),
    [
        (False, True, True),  # stdin is a pipe / an agent: nobody to press Enter
        (None, True, True),  # stdin closed (`<&-`): Python leaves sys.stdin None
        (True, False, True),  # stderr redirected: the prompt would be invisible
        (True, True, False),  # no browser here (plain SSH): Enter could open nothing
    ],
)
def test_login_prompt_shows_the_url_without_waiting_when_it_cannot_open(
    monkeypatch, stdin_tty, stderr_tty, can_open
):
    screen, events = _prompt(
        monkeypatch, stdin_tty=stdin_tty, stderr_tty=stderr_tty, can_open=can_open
    )

    assert events == []  # never waited for Enter, never tried a browser
    assert "WDJB-MJHT" in screen and _DEVICE_URL in screen  # all it takes by hand


def test_login_prompt_points_at_the_url_when_the_browser_fails_to_open(monkeypatch):
    screen, events = _prompt(monkeypatch, opens=False)

    (_, before_enter), _ = events
    assert _DEVICE_URL in screen[len(before_enter):]  # shown again after the failed open


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
#
# whoami's identity line is EFFECTIVE identity -- who you are TO THE HUB
# you're pointed at, not just your local login state (see the offline-hub
# effective-identity spec: a real github login pointed at a local offline/
# stub hub used to get a bare 401 on register with no hint why, and whoami
# showed the login as if it would work there). `HubClient` is monkeypatched
# throughout so no real HTTP/network call is ever made -- mirroring
# test_cli_m2a.py's convention.


def _fake_hub_client(mode: str | None = "github", *, unreachable: bool = False):
    """A minimal ``HubClient`` stand-in reporting a fixed ``hub_mode()`` --
    or raising ``HubUnreachable`` when ``unreachable`` -- for whoami's hub
    round-trip. No real HubClient/network involved."""
    class _Fake:
        def __init__(self, base_url):
            self.base_url = base_url

        def hub_mode(self):
            if unreachable:
                from nethackers.hubclient.client import HubUnreachable
                raise HubUnreachable(self.base_url)
            return mode
    return _Fake


def test_whoami_reports_and_exits_nonzero_when_absent(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: None)
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("github"))

    assert cli.main(["whoami"]) == 1
    assert "not logged in" in capsys.readouterr().err

    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "t"))
    assert cli.main(["whoami"]) == 0


def test_whoami_prints_login_to_stderr_by_default(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "sekrit-tok"))
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("github"))

    assert cli.main(["whoami"]) == 0

    captured = capsys.readouterr()
    assert "castiel" in captured.err
    assert captured.out == ""  # nothing on stdout unless -o json is asked for
    assert "sekrit-tok" not in captured.err  # the token itself never gets printed


def test_whoami_offline_hub_not_logged_in_shows_offline_in_caps(monkeypatch, capsys):
    # The motivating bug's other half: an offline (stub) hub only ever knows
    # its one built-in identity -- whoami must say so plainly, in caps.
    monkeypatch.setattr(cli, "_load_creds", lambda: None)
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("offline"))

    assert cli.main(["whoami"]) == 1

    err = capsys.readouterr().err
    assert "OFFLINE" in err
    assert "@" not in err  # nobody is locally logged in -- no stray identity


def test_whoami_offline_hub_logged_in_shows_mismatch_warning(monkeypatch, capsys):
    # The actual motivating bug: @vkurenkov logged in locally, pointed at a
    # local offline/stub hub -- whoami must surface that the hub can't
    # accept that identity, not just echo the local login back.
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("vkurenkov", "tok"))
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("offline"))

    assert cli.main(["whoami"]) == 0

    err = capsys.readouterr().err
    assert "@vkurenkov" in err
    assert "OFFLINE" in err
    assert "HUB_AUTH=github" in err  # the actionable fix, not just a bare warning
    assert "tok" not in err  # the token itself never leaks


def test_whoami_github_hub_logged_in_shows_plain_login(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("vkurenkov", "tok"))
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("github"))

    assert cli.main(["whoami"]) == 0

    err = capsys.readouterr().err
    assert "@vkurenkov" in err
    assert "OFFLINE" not in err


def test_whoami_unreachable_hub_prints_a_clean_line_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_creds", lambda: None)
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client(unreachable=True))

    rc = cli.main(["whoami"])

    err = capsys.readouterr().err
    assert "hub unreachable" in err
    assert "Traceback" not in err
    assert rc == 1


_STAGE_ENV_KEYS = (
    "NETHACKERS_STAGE", "NETHACKERS_STAGE_FILE", "NETHACKERS_HUB", "NETHACKERS_HUB_PORT",
    "COMPOSE_PROJECT_NAME", "NETHACKERS_DATA_ROOT", "NETHACKERS_REPO_NAME",
    "NETHACKERS_ARENA_IMAGE", "NETHACKERS_MUTATOR_IMAGE", "NETHACKERS_CLIENT_ID",
)


def test_whoami_respects_o_json(monkeypatch, tmp_path, capsys):
    # This asserts "stage"/"hub" against Stage()'s own defaults, which only
    # holds if `cli.main`'s bare `load_stage()` actually resolves to prod --
    # so make that true by construction rather than by accident: chdir to an
    # empty tmp_path (no ancestor there can ever hold a real .env.stack,
    # unlike this repo's own checkout after a `make up`/`make stack`) and
    # clear every NETHACKERS_* env key the ambient shell might carry.
    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "sekrit-tok"))
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("github"))

    assert cli.main(["whoami", "-o", "json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "login": "castiel",
        "authenticated": True,
        "stage": Stage().name,
        "hub": Stage().hub_url,
        "hub_auth": "github",
        "effective": "@castiel",
    }
    assert "sekrit-tok" not in captured.out  # token never leaks into the JSON payload either


def test_whoami_json_reports_offline_mismatch(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("vkurenkov", "sekrit-tok"))
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client("offline"))

    assert cli.main(["whoami", "-o", "json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["login"] == "vkurenkov"
    assert data["hub_auth"] == "offline"
    assert "OFFLINE" in data["effective"]
    assert "sekrit-tok" not in data["effective"]


def test_whoami_json_hub_auth_is_null_when_unreachable(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "_load_creds", lambda: None)
    monkeypatch.setattr(cli, "HubClient", _fake_hub_client(unreachable=True))

    assert cli.main(["whoami", "-o", "json"]) == 1

    data = json.loads(capsys.readouterr().out)
    assert data["hub_auth"] is None
    assert "unreachable" in data["effective"]


# --- ambient stage indicator (the spec's HARD mitigation for silent
# .env.stack discovery -- the ``_run`` startup dim line, right after
# ``parse_args``) -----------------------------------------------------------


def test_stage_indicator_is_stderr_only_and_silent_for_prod(monkeypatch, tmp_path, capsys):
    # Same isolation as test_whoami_respects_o_json just above: an empty
    # tmp_path cwd plus every NETHACKERS_* key cleared, so "prod" below means
    # the real prod defaults, not whatever this checkout's ambient
    # .env.stack/env happens to be. `logout` is used as the carrier command
    # purely because it is the cheapest no-op verb -- unlike `whoami`, its own
    # output never mentions "stage:" so it can't be confused with the
    # indicator line under test.
    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cred, "clear", lambda: None)

    # prod resolution -> the ambient indicator never fires, on either stream.
    assert cli.main(["logout"]) == 0
    captured = capsys.readouterr()
    assert "stage:" not in captured.err
    assert captured.out == ""

    # a named (non-prod) stage -> the indicator fires, and only on stderr --
    # so a real command's `-o json` stdout stays machine-clean regardless.
    monkeypatch.setenv("NETHACKERS_STAGE", "wt")
    assert cli.main(["logout"]) == 0
    captured = capsys.readouterr()
    assert f"stage: wt · hub {Stage().hub_url}" in captured.err
    assert captured.out == ""


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
    # both sandbox images are auto-provisioned; without this a clean runner
    # with no pre-built nethackers/{arena,mutator} images would shell out to a
    # real `make arena`/`make mutator` here (same pattern as test_cli_evolve.py
    # / test_offline_flag.py's autouse/inline stubs).
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "stored-tok"))

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    assert captured["owner"] == "castiel"
    assert captured["token"] == "stored-tok"


def test_evolve_falls_back_to_offline_when_no_creds_and_no_flags(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    # operator auth is a host precondition, not what these cred/flag tests cover;
    # without this they fail on any host (e.g. CI) with no claude/codex login.
    monkeypatch.setattr(cli, "sandbox_preflight", lambda operator: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: None)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    assert captured["owner"] == "offline"
    assert captured["token"] == "offline-token"


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
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    # Stored creds are present, but explicit flags must still win.
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("castiel", "stored-tok"))

    rc = cli._run([
        "evolve", "val-dwa-law-fem", "--seed", str(seed), "--workdir", str(tmp_path / "w"),
        "--owner", "explicit-owner", "--token", "explicit-token",
    ])

    assert rc == 0
    assert captured["owner"] == "explicit-owner"
    assert captured["token"] == "explicit-token"
