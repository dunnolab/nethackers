# tests/test_cli_evolve.py
import json

from nethackers import cli
from nethackers.harness import launch


def test_evolve_parses_and_invokes_loop(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    captured = {}
    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        from nethackers.harness.loop import IterationResult
        return [IterationResult(True, "registered", dev_fitness=0.6, heldout_fitness=0.6,
                                tokens=10, digest="sha256:new")]
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--operator", "claude", "--iterations", "1", "--token-budget", "5000",
                   "--token", "dev-token", "--owner", "dev", "--workdir", str(tmp_path / "w")])
    assert rc == 0
    assert captured["objective"] == "val-dwa-law-fem"
    assert captured["iterations"] == 1 and captured["token_budget"] == 5000
    assert captured["max_parallel_evals"] == 8  # default, unset here


def test_evolve_passes_max_parallel_evals(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    captured = {}
    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        from nethackers.harness.loop import IterationResult
        return [IterationResult(True, "registered", dev_fitness=0.6, heldout_fitness=0.6,
                                tokens=10, digest="sha256:new")]
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--operator", "claude", "--iterations", "1", "--token-budget", "5000",
                   "--token", "dev-token", "--owner", "dev", "--workdir", str(tmp_path / "w"),
                   "--max-parallel-evals", "4"])
    assert rc == 0
    assert captured["max_parallel_evals"] == 4


def test_evolve_creates_run_dir_with_config_and_latest_symlink(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    recorded = {}

    def fake_run_loop(**kwargs):
        recorded["tree_store_root"] = str(kwargs["tree_store"]._root)
        recorded["workdir"] = str(kwargs["workdir"])
        return []
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "random", "--seed", str(seed), "--workdir", str(tmp_path / "w")])
    assert rc == 0

    runs = tmp_path / "w" / "runs"
    created = list(runs.iterdir())
    run_dir = next(p for p in created if p.name != "latest")
    assert (run_dir / "run.json").exists()
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["objective"] == "random" and "created_at" in cfg
    # tree-store + worktrees are under the run dir, not the flat workdir:
    assert str(run_dir) in recorded["tree_store_root"]
    assert str(run_dir / "work") == recorded["workdir"]
    assert (runs / "latest").resolve() == run_dir.resolve()   # symlink points at it


def test_evolve_on_log_persists_mutation_stream(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    captured = {}

    def fake_run_loop(**kwargs):
        captured["on_log"] = kwargs["on_log"]
        return []
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "random", "--seed", str(seed), "--workdir", str(tmp_path / "w")])
    assert rc == 0
    # the CLI wraps on_log to persist each raw stream line under logs/<tag>.log
    captured["on_log"]("iter 1/3", "AGENT REASONING\n")
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    assert (run_dir / "logs" / "iter-1-3.log").read_text() == "AGENT REASONING\n"
