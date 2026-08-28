# tests/test_offline_flag.py
"""``--offline``: an explicit, always-honest no-publish gate that sits ON TOP
OF (not instead of) the existing ``owner == OFFLINE_OWNER`` backstop in
``_publisher_for``. The brief's four unit tests below pin that gate directly;
the CLI-level tests exercise ``evolve``'s ``--offline`` flag threading into
``EvolveParams`` (and from there into the loop's ``publish`` hook) plus the
one-line dim stderr note printed when a run is anonymous and NOT told to go
offline explicitly -- so a silent ``local-only`` outcome never happens."""
from nethackers import cli
from nethackers.config import OFFLINE_OWNER
from nethackers.harness import launch
from nethackers.harness.launch import EvolveParams, _publisher_for
from nethackers.hubclient import credentials as cred


def test_offline_default_false():
    assert EvolveParams(objective="mon", seed="s").offline is False


def test_publisher_none_when_offline():
    # explicit --offline: no publisher regardless of a real owner/login
    assert _publisher_for("realowner", "run1", repo_name="nh-dev-x", offline=True) is None


def test_publisher_none_for_anonymous_owner():
    # anonymous (offline) still can't publish -- the backstop remains
    assert _publisher_for(OFFLINE_OWNER, "run1", repo_name="nethacker", offline=False) is None


def test_publisher_live_when_online_and_real_owner():
    assert _publisher_for("realowner", "run1", repo_name="nh-dev-x", offline=False) is not None


# --- CLI wiring: --offline threads through EvolveParams to the loop's publish hook,
# and the anonymous-without-offline case gets exactly one honest stderr note. ------


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}')
    (seed / "bot.py").write_text("x=1\n")
    return seed


def test_cli_offline_flag_disables_publisher_even_for_a_real_owner(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("realowner", "tok"))

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w"), "--offline"])

    assert rc == 0
    assert captured["owner"] == "realowner"     # real identity, still attributed
    assert captured["publish"] is None          # but --offline forbids publishing


def test_cli_real_owner_without_offline_gets_a_live_publisher(tmp_path, monkeypatch, capsys):
    seed = _seed(tmp_path)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: cred.Credentials("realowner", "tok"))

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    assert captured["publish"] is not None
    assert "not logged in" not in capsys.readouterr().err   # a real owner needs no nudge


def test_cli_anonymous_without_offline_prints_one_dim_hint(tmp_path, monkeypatch, capsys):
    seed = _seed(tmp_path)
    monkeypatch.setattr(launch, "run_loop", lambda **kw: [])
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: None)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w")])

    assert rc == 0
    err_out = capsys.readouterr().err
    assert err_out.count("not logged in") == 1   # exactly one line -- never a silent local-only
    assert "nethackers login" in err_out


def test_cli_anonymous_with_offline_stays_quiet(tmp_path, monkeypatch, capsys):
    # --offline is itself the honest, explicit signal -- no need to also nag.
    seed = _seed(tmp_path)
    monkeypatch.setattr(launch, "run_loop", lambda **kw: [])
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_load_creds", lambda: None)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--workdir", str(tmp_path / "w"), "--offline"])

    assert rc == 0
    assert "not logged in" not in capsys.readouterr().err
