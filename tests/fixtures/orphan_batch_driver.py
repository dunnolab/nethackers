"""Driver for ``test_orphaned_children``: a batch whose episodes never finish.

Deliberately a *script*, not a helper importable from the test module:
``run_batch``'s real executor is a spawn-based ``ProcessPoolExecutor``, and a
spawn worker re-imports its parent's ``__main__`` to unpickle ``run_one``. A
closure or a test-local function could never cross that boundary (which is
why ``tests/test_run_entrypoint_m2a.py`` injects a thread pool instead) -- but
this test is *about* the process boundary, so it has to use the real one.

Prints one worker pid per line as each episode starts, then blocks in the
episode long enough for the test to kill this process out from under it.
"""

import os
import time

from nethackers.arena.run import run_batch

EPISODE_SECONDS = 600


def _block(submission_path, spec, objective, character):
    """Stand in for a real episode: announce the worker pid, then run 'long'."""
    del submission_path, spec, objective, character
    print(os.getpid(), flush=True)
    time.sleep(EPISODE_SECONDS)
    raise AssertionError("worker outlived the test")  # pragma: no cover


if __name__ == "__main__":
    run_batch(
        "/sol", [[0, "x"], [1, "x"]],
        secret="s", evaluation_id="e", max_steps=1, no_progress_timeout=1,
        action_timeout=1.0, max_parallel_evals=2, run_one=_block,
    )
