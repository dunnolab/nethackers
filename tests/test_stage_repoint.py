# tests/test_stage_repoint.py
from pathlib import Path

from nethackers.config import DEV_OWNER, DEV_TOKEN, Stage


def test_cli_hub_default_is_stage_hub_url(monkeypatch):
    monkeypatch.delenv("NETHACKERS_HUB", raising=False)
    import importlib

    from nethackers import cli
    importlib.reload(cli)
    p = cli._build_parser(Stage())
    # `board`'s objective is `--objective <name>`, never a bare positional
    # (see test_cli_m2a.py's real invocations) -- "generalist" needs the flag.
    ns = p.parse_args(["board", "--objective", "generalist"])
    # top-level --hub default flows to the subcommand via the SUPPRESS merge
    assert ns.hub == "https://nethackers.dunnolab.ai"


def test_evolveparams_defaults_from_stage(monkeypatch):
    monkeypatch.setenv("NETHACKERS_HUB", "http://localhost:8000")
    from nethackers.harness.launch import EvolveParams
    p = EvolveParams(objective="mon", seed="roots/autoascend")
    assert p.hub == "http://localhost:8000"          # from stage (env override here)
    assert p.image == "nethackers/arena:dev"
    assert p.mutator_image == "nethackers/mutator:latest"
    assert p.token == DEV_TOKEN and p.owner == DEV_OWNER
    assert p.workdir == str(Path.home() / ".nethackers" / "evolve")
    assert p.repo_name == "nethacker"                # NEW field, default from stage


def test_publisher_uses_params_repo_name():
    from nethackers.harness.launch import _publisher_for
    # real owner + custom repo name -> a live publisher (not None)
    assert _publisher_for("someone", "run1", repo_name="nh-dev-x") is not None
