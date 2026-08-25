"""Root cause ③, the trigger: concurrent arena eval batches on the same host
perturb each other's timing even with a generous per-action timeout (a
sibling run's docker evals hammering the box while this run's baseline
validation ran caused a 21% score swing on identical code). A cross-process
advisory file lock around eval_batch's docker invocation makes eval timing
stable -- this test proves two concurrent eval_batch calls never run their
"docker" step at the same time, using a fake runner (never a real Docker
daemon) and a NETHACKERS_EVAL_LOCK override so it never touches the real
~/.nethackers/eval.lock.
"""
import json
import threading
import time
from pathlib import Path

from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import eval_batch

_SPEC = ObjectiveSpec(
    name="lock-smoke",
    kind="batch",
    batch=((0, "val-dwa-law-fem"),),
    max_steps=10,
    no_progress_timeout=10,
    action_timeout_seconds=5.0,
    aggregation="mean",
)

_RESULT = [{
    "trajectory_id": 0, "status": "completed", "progress": 0.1, "ascended": False,
    "steps": 1, "turns": 1, "max_depth": 1, "end_status": "died", "error": None,
    "wall_seconds": 0.1, "character": "val-dwa-law-fem", "milestone": None,
}]


def test_eval_batch_serializes_per_host(tmp_path, monkeypatch):
    monkeypatch.setenv("NETHACKERS_EVAL_LOCK", str(tmp_path / "eval.lock"))
    active = {"n": 0, "max": 0}
    guard = threading.Lock()

    def fake_docker(cmd, check=True, **_k):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)   # simulate a slow docker run -- long enough for
        # a second, un-serialized call to overlap it if the lock is missing
        host_out = next(v.removesuffix(":/out") for v in cmd if v.endswith(":/out"))
        Path(host_out, "results.json").write_text(json.dumps(_RESULT))
        with guard:
            active["n"] -= 1

    def _run(i: int) -> None:
        sol = tmp_path / f"sol{i}"
        sol.mkdir()
        (sol / "bot.py").write_text("x")
        eval_batch(sol, _SPEC, "img:dev", now="t", runner=fake_docker,
                   image_digest_resolver=lambda img: "img@sha256:x")

    threads = [threading.Thread(target=_run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert active["max"] == 1   # never two docker runs at once
