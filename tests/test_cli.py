"""Exercises ``nethackers.cli.main``'s subcommand wiring for ``eval`` and
``pull`` with both ``eval_batch`` and ``pull`` monkeypatched on the ``cli``
module -- no real Docker or git process is ever invoked here.

``eval`` resolves ``--objective <name>`` against the real
``nethackers.hub.objectives.CATALOG`` and calls ``eval_batch`` with the
resolved ``ObjectiveSpec`` -- the loop-closing regression proving that
evidence is actually *registerable* lives in tests/test_cli_m2a.py
alongside the rest of the M2a hub-facing CLI coverage.
"""

import json

import nethackers.cli as C
from nethackers.config import load_stage
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.objectives import CATALOG


def test_cli_eval_invokes_eval_batch_with_resolved_objective(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_eval_batch(solution, spec, image, *, now, max_parallel_evals=8):
        seen["solution"] = solution
        seen["spec"] = spec
        seen["image"] = image
        seen["now"] = now
        seen["max_parallel_evals"] = max_parallel_evals
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(["eval", str(tmp_path), "--objective", "val-dwa-law-fem"])

    assert rc == 0
    # Resolves the real catalog entry -- not a hand-rolled Objective.
    assert seen["spec"] is CATALOG["val-dwa-law-fem"]
    assert seen["solution"] == tmp_path
    assert seen["image"] == "nethackers/arena:dev"
    assert seen["max_parallel_evals"] == 8  # default, unset here

    out = json.loads(capsys.readouterr().out)
    assert out["mean_progress"] == 0.1
    assert out["evaluator_image"] == "nethackers/arena:dev"
    assert out["objective"]["seed_set"] == "val-dwa-law-fem"


def test_cli_eval_custom_image_is_passed_through(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_eval_batch(solution, spec, image, *, now, max_parallel_evals=8):
        seen["image"] = image
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(
        ["eval", str(tmp_path), "--objective", "random", "--image", "custom/arena:tag"]
    )

    assert rc == 0
    assert seen["image"] == "custom/arena:tag"


def test_cli_eval_custom_max_parallel_evals_is_passed_through(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_eval_batch(solution, spec, image, *, now, max_parallel_evals=8):
        seen["max_parallel_evals"] = max_parallel_evals
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(
        ["eval", str(tmp_path), "--objective", "random", "--max-parallel-evals", "3"]
    )

    assert rc == 0
    assert seen["max_parallel_evals"] == 3


def test_cli_eval_unknown_objective_errors_without_traceback(capsys, tmp_path):
    rc = C.main(["eval", str(tmp_path), "--objective", "not-a-real-objective"])

    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing printed to stdout
    assert "not-a-real-objective" in captured.err


# --- bare `nethackers` entry rule -------------------------------------
#
# Bare `nethackers` on a TTY opens the dashboard TUI (Task 14); piped or
# `--no-tui` prints help exactly like every other invocation, never emitting
# Textual escape codes into a pipe. `C.NetHackersApp` is monkeypatched to a
# recording fake in every TTY case below -- the real Textual app must never
# be constructed or run by this suite.


def test_bare_piped_prints_help(monkeypatch, capsys):
    monkeypatch.setattr(C.sys.stdout, "isatty", lambda: False)

    assert C.main([]) == 0

    assert "usage" in capsys.readouterr().out.lower()


def test_bare_tty_launches_app(monkeypatch):
    launched = {}
    monkeypatch.setattr(C.sys.stdout, "isatty", lambda: True)

    class FakeApp:
        def __init__(self, *a, **k):
            launched["hub"] = k.get("hub") or (a[0] if a else None)

        def run(self):
            launched["ran"] = True

        error = None

    monkeypatch.setattr(C, "NetHackersApp", FakeApp)

    assert C.main([]) == 0

    assert launched.get("ran") is True
    assert launched.get("hub") == load_stage().hub_url  # the resolved top-level --hub, not None


def test_bare_no_tui_prints_help_and_never_constructs_the_app(monkeypatch, capsys):
    launched = {}
    monkeypatch.setattr(C.sys.stdout, "isatty", lambda: True)  # a TTY -- --no-tui must still win

    class FakeApp:
        def __init__(self, *a, **k):
            launched["constructed"] = True

        def run(self):
            launched["ran"] = True

        error = None

    monkeypatch.setattr(C, "NetHackersApp", FakeApp)

    assert C.main(["--no-tui"]) == 0

    assert "usage" in capsys.readouterr().out.lower()
    assert launched == {}  # NetHackersApp never constructed, let alone run


def test_cli_pull_invokes_pull(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_pull(repo_at_commit, dest):
        seen["repo_at_commit"] = repo_at_commit
        seen["dest"] = dest
        return dest

    monkeypatch.setattr(C, "pull", fake_pull)
    dest = tmp_path / "d"

    rc = C.main(["pull", "dunnolab/nethacker@abc123", str(dest)])

    assert rc == 0
    assert seen == {"repo_at_commit": "dunnolab/nethacker@abc123", "dest": dest}
    assert capsys.readouterr().out.strip() == str(dest)


# --- default hub URL --------------------------------------------------


# Every NETHACKERS_* key load_stage() reads -- cleared below, same isolation
# tests/test_config.py's test_prod_flag_bypasses_discovery and
# tests/test_cli_login.py's test_whoami_respects_o_json already use.
_STAGE_ENV_KEYS = (
    "NETHACKERS_STAGE", "NETHACKERS_STAGE_FILE", "NETHACKERS_HUB", "NETHACKERS_HUB_PORT",
    "COMPOSE_PROJECT_NAME", "NETHACKERS_DATA_ROOT", "NETHACKERS_REPO_NAME",
    "NETHACKERS_ARENA_IMAGE", "NETHACKERS_MUTATOR_IMAGE", "NETHACKERS_CLIENT_ID",
)


def test_default_hub_is_prod(tmp_path, monkeypatch):
    # cwd=tmp_path (an empty dir, never an ancestor of a real .env.stack)
    # plus every NETHACKERS_* key cleared -- load_stage() is called directly
    # here, so the cwd seam is passed straight through rather than chdir'd.
    from nethackers import cli
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    parser = cli._build_parser(load_stage(cwd=tmp_path))
    assert parser.parse_args([]).hub == "https://nethackers.dunnolab.ai"
