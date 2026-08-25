# tests/test_evolve_launch.py
import json
from pathlib import Path

from nethackers.harness import launch
from nethackers.harness.launch import EvolveParams, prepare_evolve


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
