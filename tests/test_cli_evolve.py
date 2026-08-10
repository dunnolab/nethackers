# tests/test_cli_evolve.py
from nethackers import cli


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
    monkeypatch.setattr(cli, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--operator", "claude", "--iterations", "1", "--token-budget", "5000",
                   "--token", "dev-token", "--owner", "dev", "--workdir", str(tmp_path / "w")])
    assert rc == 0
    assert captured["objective"] == "val-dwa-law-fem"
    assert captured["iterations"] == 1 and captured["token_budget"] == 5000
