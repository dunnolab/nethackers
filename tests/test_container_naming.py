import contextlib
import re

from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import eval_batch
from nethackers.harness import container_operator as co, discovery


def _names(argv):
    return argv[argv.index("--name") + 1] if "--name" in argv else None


def test_mutator_name_is_prefixed():
    # the mutator keeps its inspectable run/iter suffix, gains the prefix
    assert co._mutator_container_name("run7", "iter-3") == "nethackers-mut-run7-iter-3"


def test_build_docker_argv_has_label():
    argv = co.build_docker_argv(
        harness="claude", image="img", name="nethackers-mut-x",
        worktree="/w", cli="claude", model=None, effort=None,
        caps=co.ContainerCaps(pids=1, memory="1g", cpus="1", timeout_s=1),
        auth_args=[], brief="b",
    )
    assert "--label" in argv and argv[argv.index("--label") + 1] == "nethackers"


def test_discovery_probe_names_and_labels():
    captured = {}
    def fake_run(argv, **kw):
        captured["argv"] = argv
        class P:  # minimal stand-in
            stdout = ""
        return P()
    discovery._run_image_script("img", "codex", "true", run=fake_run)
    argv = captured["argv"]
    assert re.fullmatch(r"nethackers-probe-[0-9a-f]{8}", _names(argv))
    assert "--label" in argv and argv[argv.index("--label") + 1] == "nethackers"


def test_arena_run_named_and_labeled(tmp_path):
    # eval_batch refuses a directory that is not a solution root, so give it
    # the one file the arena loads a bot from before checking the argv.
    (tmp_path / "bot.py").write_text("def make_agent(): ...\n")
    captured = {}

    def fake_runner(cmd, **kw):
        captured["cmd"] = cmd
        raise SystemExit  # stop before reading results -- we only need the argv

    spec = ObjectiveSpec(
        name="t", kind="tier1", batch=((0, "val-dwa-law-fem"),),
        max_steps=1, no_progress_timeout=1, action_timeout_seconds=1.0,
        aggregation="mean",
    )
    with contextlib.suppress(SystemExit):
        eval_batch(tmp_path, spec, "img", now="t", max_parallel_evals=1,
                   runner=fake_runner, image_digest_resolver=lambda i: i)
    cmd = captured["cmd"]
    assert re.fullmatch(r"nethackers-arena-[0-9a-f]{8}", cmd[cmd.index("--name") + 1])
    assert "--label" in cmd and cmd[cmd.index("--label") + 1] == "nethackers"
