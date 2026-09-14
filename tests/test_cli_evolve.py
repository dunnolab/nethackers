# tests/test_cli_evolve.py
import json

import pytest

from nethackers import cli
from nethackers.harness import launch
from nethackers.harness.discovery import CliInfo, Preflight

# run_loop is called from harness.launch.prepare_evolve (the shared CLI +
# in-app evolve setup), so these wiring tests patch it there, not on `cli`.
# There is no pre-loop SELECT anymore -- the MAP-Elites loop seeds its cells
# from the hub itself -- so --from-seed just makes the run hermetic (no hub).


@pytest.fixture(autouse=True)
def _sandbox_preflight_ok(monkeypatch):
    # evolve now ALWAYS sandboxes, so every run first clears the sandbox
    # preflight (a working container runtime + a resolvable login). These are
    # wiring tests (run_loop is faked and no container is ever launched), so
    # stub the preflight green; its real behaviour is covered in
    # test_sandbox_preflight.py and test_cli_sandbox.py.
    monkeypatch.setattr(cli, "sandbox_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "image_present", lambda *a, **kw: True)   # sandbox image ready


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
        return [IterationResult(True, "registered", dev_fitness=0.6,
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
        return [IterationResult(True, "registered", dev_fitness=0.6,
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
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0

    runs = tmp_path / "w" / "runs"
    created = list(runs.iterdir())
    run_dir = next(p for p in created if p.name != "latest")
    assert (run_dir / "run.json").exists()
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["objective"] == "val-dwa-law-fem" and "created_at" in cfg
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
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 0
    # the CLI wraps on_log to persist each raw stream line under logs/<tag>.log
    captured["on_log"]("iter 1/3", "AGENT REASONING\n")
    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    assert (run_dir / "logs" / "iter-1-3.log").read_text() == "AGENT REASONING\n"


def test_evolve_passes_seed_straight_through_and_records_parent_seed(tmp_path, monkeypatch):
    # No pre-loop SELECT: the loop seeds its cells from the hub itself, so
    # launch hands the --seed tree straight to run_loop and run.json records
    # parent == "seed" (the cold-start marker).
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
    assert rc == 0
    assert captured["seed_tree"] == seed

    runs = tmp_path / "w" / "runs"
    run_dir = next(p for p in runs.iterdir() if p.name != "latest")
    cfg = json.loads((run_dir / "run.json").read_text())
    assert cfg["parent"] == "seed"


def test_evolve_rejects_removed_flags(tmp_path):
    # --islands / --reset-period were removed with the island search (MAP-Elites
    # has no islands); --select-k / --select-temp were removed with select_parent
    # (MAP-Elites picks a random cell, not a SELECT sample); --validation-n was
    # removed with the validation gate (MAP-Elites has no validation eval);
    # --token-budget / --timeout were removed earlier. argparse must reject
    # every one of them.
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root":".","entrypoint":"bot.py","parents":[],"influences":[]}'
    )
    (seed / "bot.py").write_text("x=1\n")
    for removed in ("--token-budget", "--timeout", "--islands", "--reset-period",
                    "--select-k", "--select-temp", "--validation-n"):
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


def test_evolve_refuses_opencode2_effort_without_a_model(tmp_path, monkeypatch):
    # OpenCode 2 applies effort only as `provider/model#variant`. Without a
    # model it was silently dropped, while run.json still recorded it.
    seed = _seed(tmp_path)
    called = {"prepared": False}
    monkeypatch.setattr(cli, "prepare_evolve",
                        lambda *a, **k: called.update(prepared=True), raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--operator", "opencode2", "--effort", "high",
                   "--workdir", str(tmp_path / "w")])
    assert rc == 2
    assert called["prepared"] is False


def test_evolve_skips_preflight_without_a_pinned_model(tmp_path, monkeypatch):
    seed = _seed(tmp_path)
    fired = {"preflight": False}
    monkeypatch.setattr(cli, "preflight_model",
                        lambda *a, **k: fired.update(preflight=True))
    monkeypatch.setattr(launch, "run_loop", lambda **k: [], raising=False)
    rc = cli._run(["evolve", "val-dwa-law-fem", "--seed", str(seed), "--from-seed",
                   "--workdir", str(tmp_path / "w")])       # no --model
    assert rc == 0 and fired["preflight"] is False          # guarded by `if args.model`
