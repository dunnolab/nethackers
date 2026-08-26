# tests/test_evolve_launch.py
import json
from pathlib import Path

from nethackers.harness import launch
from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.version import HARNESS_VERSION


def test_prepare_evolve_writes_config_and_drives_run_loop(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or ["res"])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-15T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     operator="claude", iterations=2, from_seed=True,
                     workdir=str(tmp_path), hub="http://h", token="tok", owner="castiel")
    plan = prepare_evolve(p, git_sha="deadbeef")
    cfg_json = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg_json["objective"] == "wiz-elf-cha-mal"
    assert cfg_json["operator"] == "claude" and cfg_json["git_sha"] == "deadbeef"
    assert plan.cfg.objective == "wiz-elf-cha-mal" and plan.cfg.iterations == 2
    results = plan.run({"on_state": lambda s: None,
                        "on_episode": lambda label, ep: None, "on_log": lambda tag, line: None})
    assert results == ["res"]
    assert captured["objective"] == "wiz-elf-cha-mal"
    assert captured["iterations"] == 2
    assert captured["islands"] == 1 and captured["reset_period"] is None  # defaults
    assert captured["validation_n"] == 15  # default
    assert captured["owner"] == "castiel" and captured["token"] == "tok"
    assert (Path(tmp_path) / "runs" / "latest").resolve().name == plan.rid


def _run_kwargs():
    return {"on_state": lambda s: None,
            "on_episode": lambda label, ep: None, "on_log": lambda tag, line: None}


def test_prepare_evolve_attaches_token_source_when_creds_exist(tmp_path, monkeypatch):
    # The M2 bug this closes: evolve used to hand run_loop a raw, possibly-
    # already-expired access_token; the hub 401s it and the win silently
    # stayed local-only. prepare_evolve must instead build the run's
    # HubClient with a TokenSource wired from the stored credential, so
    # register() can refresh+retry on its own.
    from nethackers.hubclient.auth import TokenSource
    from nethackers.hubclient.credentials import Credentials
    creds = Credentials("sam", "ghu_a", "ghr_b", expires_at=1000.0)
    monkeypatch.setattr(launch._credentials, "load", lambda: creds)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-26T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     from_seed=True, workdir=str(tmp_path), hub="http://h",
                     token="tok", owner="sam")

    prepare_evolve(p, git_sha="deadbeef").run(_run_kwargs())

    hub = captured["hub"]
    assert isinstance(hub._token_source, TokenSource)
    assert hub._token_source.login == "sam"
    # run_loop's own token= is untouched -- it's only the fallback used when
    # register() has no token_source (see the no-creds test below).
    assert captured["token"] == "tok"


def test_prepare_evolve_no_token_source_without_stored_creds(tmp_path, monkeypatch):
    # Back-compat: dev/test with nothing logged in -> no source, register()
    # falls back to plain token="dev-token" (or whatever was passed), exactly
    # like before this adapter existed.
    monkeypatch.setattr(launch._credentials, "load", lambda: None)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-26T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     from_seed=True, workdir=str(tmp_path), hub="http://h",
                     token="dev-token", owner="dev")

    prepare_evolve(p, git_sha="deadbeef").run(_run_kwargs())

    assert captured["hub"]._token_source is None
    assert captured["token"] == "dev-token"


def test_prepare_evolve_shares_one_hub_client_between_select_and_run_loop(tmp_path, monkeypatch):
    # SELECT and registration should go through the SAME client -- not two
    # independently-constructed HubClients -- so there's exactly one place
    # auth (or lack of it) is decided for the whole run.
    monkeypatch.setattr(launch._credentials, "load", lambda: None)
    seed = tmp_path / "seed"
    seed.mkdir()
    seen = {}

    def fake_select_parent(hub, objective, store, seed_tree, *, owner, k, temperature, rng):
        seen["select_hub"] = hub
        return seed_tree, "sha256:elite"
    monkeypatch.setattr(launch, "select_parent", fake_select_parent, raising=False)
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-26T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed=str(seed),
                     workdir=str(tmp_path), hub="http://h", token="t", owner="o")

    prepare_evolve(p, git_sha="x").run(_run_kwargs())

    assert seen["select_hub"] is captured["hub"]


def test_prepare_evolve_iterations_are_per_island(tmp_path, monkeypatch):
    """iterations is per-island: run_loop's total cap, EvolveConfig, and
    run.json all carry iterations × islands; iterations_per_island keeps the
    input value."""
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-25T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     iterations=5, islands=3, from_seed=True,
                     workdir=str(tmp_path), hub="http://h", token="t", owner="o")
    plan = prepare_evolve(p, git_sha="x")
    assert plan.cfg.iterations == 15   # 5 per island × 3 islands
    plan.run({"on_state": lambda s: None,
              "on_episode": lambda label, ep: None, "on_log": lambda tag, line: None})
    assert captured["iterations"] == 15 and captured["islands"] == 3
    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["iterations"] == 15 and cfg["iterations_per_island"] == 5


def test_harness_version_is_v1():
    assert HARNESS_VERSION == "v1"


def test_run_config_records_harness_version(tmp_path):
    from nethackers.harness.launch import EvolveParams, prepare_evolve
    from nethackers.harness.store import LocalTreeStore
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root": ".", "entrypoint": "bot.py"}')
    plan = prepare_evolve(
        EvolveParams(objective="val-dwa-law-fem", seed=str(seed),
                     workdir=str(tmp_path / "wd"), owner="dev", from_seed=True),
        tree_store=LocalTreeStore(tmp_path / "store"))
    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["harness_version"] == HARNESS_VERSION
