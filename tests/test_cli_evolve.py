# tests/test_cli_evolve.py
import json

import pytest

from nethackers import cli
from nethackers.harness import launch
from nethackers.harness.discovery import CliInfo, Preflight

# run_loop + select_parent are called from harness.launch.prepare_evolve (the
# shared CLI + in-app evolve setup), so these wiring tests patch them there,
# not on `cli`. --from-seed forces a cold start so the SELECT path never
# touches a real hub (hermetic), except the two tests that exercise SELECT.


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
        return [IterationResult(True, "registered", dev_fitness=0.6, validation_fitness=0.6,
                                tokens=10, digest="sha256:new")]
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--operator", "claude", "--iterations", "1",
                   "--token", "dev-token", "--owner", "dev", "--workdir", str(tmp_path / "w")])
    assert rc == 0
    assert captured["objective"] == "val-dwa-law-fem"
    assert captured["iterations"] == 1
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
        return [IterationResult(True, "registered", dev_fitness=0.6, validation_fitness=0.6,
                                tokens=10, digest="sha256:new")]
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--operator", "claude", "--iterations", "1",
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
    rc = cli._run(["evolve", "random", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0

    runs = tmp_path / "w" / "runs"
    created = list(runs.iterdir())
    run_dir = next(p for p in created if p.name != "latest")
    assert (run_dir / "run.json").exists()
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["objective"] == "random" and "created_at" in cfg
    # tree-store is shared machine-wide (content cache, dedup by digest) --
    # NOT per-run; only the worktrees + records stay under the run dir.
    assert recorded["tree_store_root"] == str(tmp_path / "w" / "store")
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
    rc = cli._run(["evolve", "random", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0
    # the CLI wraps on_log to persist each raw stream line under logs/<tag>.log
    captured["on_log"]("iter 1/3", "AGENT REASONING\n")
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    assert (run_dir / "logs" / "iter-1-3.log").read_text() == "AGENT REASONING\n"


def test_evolve_selects_parent_from_hub_and_records_it(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    elite_tree = tmp_path / "elite"
    elite_tree.mkdir()
    (elite_tree / "bot.py").write_text("elite=1\n")

    captured = {}
    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)

    select_calls = []
    def fake_select_parent(hub, objective, store, seed_tree, *, owner, k, temperature, rng):
        select_calls.append({
            "objective": objective, "seed_tree": seed_tree, "owner": owner,
            "k": k, "temperature": temperature, "rng": rng,
        })
        return elite_tree, "sha256:elite"
    monkeypatch.setattr(launch, "select_parent", fake_select_parent, raising=False)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed),
                   "--owner", "dev", "--workdir", str(tmp_path / "w")])
    assert rc == 0

    # select_parent was called (default --select-k/--select-temp) and its
    # resolved tree -- not --seed -- is exactly what run_loop received.
    assert len(select_calls) == 1
    call = select_calls[0]
    assert call["objective"] == "val-dwa-law-fem" and call["owner"] == "dev"
    assert call["k"] == 1 and call["temperature"] == 1.0
    assert call["seed_tree"] == seed
    assert captured["seed_tree"] == elite_tree

    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["parent"] == "sha256:elite"
    assert cfg["select_k"] == 1
    assert cfg["select_temp"] == 1.0


def test_evolve_from_seed_bypasses_select_parent(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")

    captured = {}
    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)

    def fake_select_parent(*args, **kwargs):
        raise AssertionError("select_parent must not be called with --from-seed")
    monkeypatch.setattr(launch, "select_parent", fake_select_parent, raising=False)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0
    assert captured["seed_tree"] == seed

    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["parent"] == "seed"


def test_evolve_migrate_defaults_on_and_records_it(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    captured = {}

    def fake_run_loop(**kwargs):
        captured.update(kwargs)
        return []
    monkeypatch.setattr(launch, "run_loop", fake_run_loop, raising=False)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0 and captured["migrate"] is True
    run_dir = next(p for p in (tmp_path / "w" / "runs").iterdir() if p.name != "latest")
    assert json.loads((run_dir / "run.json").read_text())["migrate"] is True


def test_evolve_no_migrate_flag_disables(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    captured = {}
    monkeypatch.setattr(launch, "run_loop",
                        lambda **k: captured.update(k) or [], raising=False)

    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--no-migrate", "--workdir", str(tmp_path / "w")])
    assert rc == 0 and captured["migrate"] is False


def test_evolve_rejects_removed_budget_and_timeout_flags(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    for removed in ("--token-budget", "--timeout"):
        with pytest.raises(SystemExit):   # argparse rejects the deleted flag
            cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                      removed, "1", "--workdir", str(tmp_path / "w")])


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}')
    (seed / "bot.py").write_text("x=1\n")
    return seed


def test_evolve_refuses_unavailable_model(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    called = {"prepared": False}
    monkeypatch.setattr(cli, "prepare_evolve",
                        lambda *a, **k: called.update(prepared=True), raising=False)
    monkeypatch.setattr(cli, "preflight_model", lambda *a, **k: Preflight(
        "refuse", "codex 0.50.0 can't serve 'gpt-5.6-sol'.",
        CliInfo("codex", True, "codex-cli 0.50.0", True), []))
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--operator", "codex", "--model", "gpt-5.6-sol",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 2
    assert called["prepared"] is False        # never built a run for a doomed model


def test_evolve_skips_preflight_without_a_pinned_model(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    fired = {"preflight": False}
    monkeypatch.setattr(cli, "preflight_model",
                        lambda *a, **k: fired.update(preflight=True))
    monkeypatch.setattr(launch, "run_loop", lambda **k: [], raising=False)
    rc = cli._run(["evolve", "random", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])       # no --model
    assert rc == 0 and fired["preflight"] is False          # guarded by `if args.model`
