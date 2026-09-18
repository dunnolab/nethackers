# tests/test_evolve_launch.py
import json
from pathlib import Path

from nethackers.harness import launch
from nethackers.harness.launch import EvolveParams, prepare_evolve
from nethackers.harness.version import RUN_SCHEMA_VERSION


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
    # islands/reset_period/validation_n are retired -- run_loop is never handed
    # any of them now (MAP-Elites has no island search or validation gate).
    assert "islands" not in captured and "validation_n" not in captured
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


def test_prepare_evolve_builds_one_authed_hub_client_for_the_run(tmp_path, monkeypatch):
    # There's a single HubClient for the whole run (registration + the loop's
    # own cell-seeding reads go through it), built once via _authed_hub. The
    # pre-loop SELECT is gone, so the loop seeds cells from this same hub.
    from nethackers.hubclient.client import HubClient
    monkeypatch.setattr(launch._credentials, "load", lambda: None)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "bot.py").write_text("def make_agent(): ...\n")
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-26T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed=str(seed),
                     workdir=str(tmp_path), hub="http://h", token="t", owner="o")

    prepare_evolve(p, git_sha="x").run(_run_kwargs())

    assert isinstance(captured["hub"], HubClient)


def test_prepare_evolve_iterations_pass_through_unchanged(tmp_path, monkeypatch):
    """islands is retired: iterations is the literal round count run_loop gets,
    EvolveConfig carries, and run.json records -- no ×islands multiplication and
    no iterations_per_island key."""
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-08-25T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     iterations=5, from_seed=True,
                     workdir=str(tmp_path), hub="http://h", token="t", owner="o")
    plan = prepare_evolve(p, git_sha="x")
    assert plan.cfg.iterations == 5   # not 15 -- no ×islands multiplication
    plan.run({"on_state": lambda s: None,
              "on_episode": lambda label, ep: None, "on_log": lambda tag, line: None})
    assert captured["iterations"] == 5 and "islands" not in captured
    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["iterations"] == 5 and "iterations_per_island" not in cfg


def test_run_schema_version_is_v1():
    assert RUN_SCHEMA_VERSION == "v1"


def test_run_config_records_run_schema_version(tmp_path):
    from nethackers.harness.launch import EvolveParams, prepare_evolve
    from nethackers.harness.store import LocalTreeStore
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root": ".", "entrypoint": "bot.py"}')
    (seed / "bot.py").write_text("def make_agent(): ...\n")
    plan = prepare_evolve(
        EvolveParams(objective="val-dwa-law-fem", seed=str(seed),
                     workdir=str(tmp_path / "wd"), owner="dev", from_seed=True),
        tree_store=LocalTreeStore(tmp_path / "store"))
    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["run_schema_version"] == RUN_SCHEMA_VERSION


def test_run_forwards_on_iteration_and_still_writes_metric(tmp_path, monkeypatch):
    from nethackers.harness.loop import IterationResult
    monkeypatch.setattr(launch, "_now", lambda: "2026-09-06T00:00:00+00:00")

    def fake_run_loop(**kw):
        kw["on_iteration"](1, IterationResult(True, "registered", dev_fitness=0.4,
                                              improved=["val-dwa-law-fem"]))
        return ["res"]
    monkeypatch.setattr(launch, "run_loop", fake_run_loop)
    p = EvolveParams(objective="val-dwa-law-fem", seed="roots/autoascend",
                     iterations=1, from_seed=True, workdir=str(tmp_path),
                     hub="http://h", token="t", owner="dev")
    plan = prepare_evolve(p)
    seen = []
    plan.run({"on_state": lambda s: None, "on_episode": lambda label, ep: None,
              "on_log": lambda tag, line: None,
              "on_iteration": lambda it, res: seen.append((it, res))})
    assert seen and seen[0][0] == 1 and seen[0][1].improved == ["val-dwa-law-fem"]
    lines = (plan.run_dir / "metrics.jsonl").read_text().splitlines()
    assert any(json.loads(x)["outcome"] == "registered" for x in lines)  # disk unchanged


def test_prepare_evolve_passes_tier_through_to_run_loop(tmp_path, monkeypatch):
    # Task 12: EvolveParams.tier must reach run_loop's own `tier` kwarg --
    # defaults to "self-reported" untouched, but an explicit "verified" pin
    # (the opt-in to cold-start off the trusted network) must thread through.
    captured = {}
    monkeypatch.setattr(launch, "run_loop", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(launch, "_now", lambda: "2026-09-18T00:00:00+00:00")
    p = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                     from_seed=True, workdir=str(tmp_path), hub="http://h",
                     token="t", owner="o")
    assert p.tier == "self-reported"   # the default, unset
    prepare_evolve(p, git_sha="x").run(_run_kwargs())
    assert captured["tier"] == "self-reported"

    captured.clear()
    p2 = EvolveParams(objective="wiz-elf-cha-mal", seed="roots/autoascend",
                      from_seed=True, workdir=str(tmp_path), hub="http://h",
                      token="t", owner="o", tier="verified")
    prepare_evolve(p2, git_sha="x").run(_run_kwargs())
    assert captured["tier"] == "verified"
