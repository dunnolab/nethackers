import json
from pathlib import Path

from nethackers.harness.evaluate import evaluate
from nethackers.harness.seeds import dev_spec


def _fake_runner(results):
    def fake(cmd, check):
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps(results))
    return fake

def _result(seed, char, progress):
    return {"trajectory_id": seed, "status": "completed", "progress": progress,
            "ascended": False, "steps": 1, "turns": 1, "max_depth": 1,
            "end_status": "died", "error": None, "wall_seconds": 0.1,
            "character": char, "milestone": None}

def test_evaluate_returns_mean_progress_and_evidence(tmp_path):
    sol = tmp_path / "sol"
    sol.mkdir()
    (sol / "bot.py").write_text("x")
    spec = dev_spec("val-dwa-law-fem")
    results = [_result(s, c, 0.4) for s, c in spec.batch]
    results[0]["progress"] = 0.6  # mean shifts off 0.4
    fitness, ev = evaluate(sol, spec, "img:dev", now="2026-08-10T00:00:00Z",
                           runner=_fake_runner(results))
    assert fitness == ev.mean_progress
    assert 0.4 < fitness < 0.6
    assert ev.episodes == len(spec.batch)
