"""Gated determinism + speedup check for parallel eval (M3): running the
same batch through the real arena image at ``max_parallel_evals=1`` vs. a
higher value must produce bit-identical per-episode results -- each
trajectory keeps its own HMAC seed (``arena/seeds.py``'s ``trajectory_spec``
is keyed by ``trajectory_id`` alone, independent of execution concurrency)
-- and the parallel run must be meaningfully faster.

Builds no image itself -- requires ``nethackers/arena:dev`` to already exist
locally, built from the Task 7-pinned Dockerfile (single-threaded BLAS --
see ``arena/Dockerfile``'s ``ENV`` block) so oversubscribed BLAS threads
don't perturb floating-point results under concurrent workers, and a
working Docker daemon with the real NLE-backed image (e.g. ``docker build
-t nethackers/arena:dev -f arena/Dockerfile .``).

Collection-safe: only stdlib + pytest + this repo's own modules are
imported at module scope (mirrors tests/test_docker_smoke.py) -- nothing
here imports docker or nle, and no subprocess is launched until the test
body runs, so this file collects cleanly even without Docker or NLE
installed. Excluded from routine runs via the ``docker``/``nle`` markers
(``uv run pytest -m "not nle and not docker"``).
"""

import time
from pathlib import Path

import pytest

from nethackers.eval.runner import eval_batch
from nethackers.harness.seeds import validation_spec

REPO_ROOT = Path(__file__).parents[1]
SOLUTION = REPO_ROOT / "src" / "nethackers" / "roots" / "autoascend"
IMAGE = "nethackers/arena:dev"

# A small, deterministic, single-identity batch: 4 episodes of the same
# character, on a seed range (start=90_000) well clear of any published
# dev/validation/smoke batch so it can never collide with one. max_steps is
# capped well below the catalog default so both runs finish in reasonable
# wall-clock time -- this is a smoke check, not a full evaluation.
_SPEC = validation_spec("val-dwa-law-fem", n=4, start=90_000, max_steps=200)


@pytest.mark.docker
@pytest.mark.nle
def test_parallel_eval_matches_serial_and_is_faster():
    t0 = time.monotonic()
    serial = eval_batch(
        SOLUTION, _SPEC, IMAGE, now="t",
        image_digest_resolver=lambda i: i, max_parallel_evals=1,
    )
    t_serial = time.monotonic() - t0

    t0 = time.monotonic()
    parallel = eval_batch(
        SOLUTION, _SPEC, IMAGE, now="t",
        image_digest_resolver=lambda i: i, max_parallel_evals=4,
    )
    t_par = time.monotonic() - t0

    def key(ev):
        # Order-independent: results come back in batch order either way,
        # but sorting makes the comparison robust to that detail too.
        return sorted(
            (r.character, r.progress, r.status, r.turns, r.max_depth) for r in ev.results
        )

    # Bit-identical results: each trajectory's seed is derived solely from
    # (secret, evaluation_id, trajectory_id) -- concurrency must not perturb
    # outcomes (this is what arena/Dockerfile's single-threaded BLAS pins
    # protect against).
    assert key(serial) == key(parallel)
    # Meaningfully faster -- a generous margin (0.8x, not e.g. 0.5x) to
    # avoid flakiness on a loaded machine while still catching a fan-out
    # that isn't actually running concurrently.
    assert t_par < 0.8 * t_serial
