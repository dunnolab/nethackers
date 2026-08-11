# Parallel eval throughput (design)

**Status:** Approved in brainstorming 2026-08-11; implementation on branch `m3/eval-throughput`. Speeds up the M3 evolve loop's inner evaluation.

## 1. Problem

Every evolve iteration fires three `docker run`s — cheap-gate smoke (1 episode) + dev batch (8) + held-out batch (8, when dev improves) — and inside each container `arena/run.py` runs the batch as a **sequential for-loop** (`run.py:64`), one `(seed, character)` episode after another. Episodes routinely run thousands of steps (minutes each), so a single iteration's eval is tens of minutes of wall-clock that pins one core while the rest idle. This is the loop's dominant latency and the main blocker to longer/broader searches.

Episodes are **fully independent** — each is a fresh NLE env + fresh bot instance seeded by its own HMAC-derived `(core, display, level, bot)` seed — so they are embarrassingly parallel. The sibling arena already exploits this (`evaluate.py` fans one subprocess per trajectory across `workers = CPU count`); we run everything serially.

## 2. Goal & scope

**Goal:** evaluate a batch's episodes concurrently on the local machine, cutting an eval's wall-clock from ~sum-of-episodes toward ~longest-episode.

**In scope:** parallelize the episodes *within* a single `eval_batch` call (dev, held-out, smoke), locally, in one container.

**Out of scope (named deliberately):**
- Multi-container / cluster (`awl`) fan-out — considered, rejected for a local target (per-container cold start × N, orchestration overhead); revisit when cluster scale is a real need.
- Evaluating multiple *candidates* per round concurrently — a search-strategy change (population-based), separate from eval throughput.
- Running dev and held-out concurrently — held-out is conditional on dev improving, so they are sequential by design; not worth speculative held-out compute.

## 3. Design (approach A: in-container worker pool)

### Concurrency model
`arena/run.py` runs its batch through a `concurrent.futures.ProcessPoolExecutor(max_workers = P)` where **P = min(--max-parallel-evals, len(batch))**. Worker *processes* (not threads) give crash isolation: an NLE segfault or hang fails only that episode, not the batch. Each worker runs `run_trajectory(...)` for its assigned pair(s) and returns a `TrajectoryResult`.

### The knob — `--max-parallel-evals`
A user-set cap on concurrent episodes/envs, **decoupled from core count**. The throughput-optimal concurrency for NLE+AutoAscend is not `nproc` — the per-episode profile isn't a steady one-core burn (thread/queue handoff between the adapter and the bot, lazy JIT, NLE stepping) — so it is an empirically tuned dial, not a derived value. If a batch has fewer episodes than the cap, only that many run. Threaded through unchanged:

`cli.py --max-parallel-evals P` → `eval_batch(..., max_parallel_evals=P)` → `docker run … -m nethackers.arena.run --max-parallel-evals P` → the pool.

Docker `--cpus` stays **orthogonal** — it is the container's resource cap, not the concurrency dial; we neither derive one from the other nor touch Docker's config (an optional `--cpus` passthrough can be added later).

### Results & ordering
Each future is submitted with its batch index; results are collected **by index** into a list, and `results.json` is written in **batch order**, exactly as today — so `eval_batch`'s result-reading and the downstream `Evidence` wrapping are unchanged.

### Live streaming & TUI
Each episode emits its `arena · episode i/N …` stderr line **as its future completes** (via `as_completed`) — out-of-order, tagged with its true batch index. `eval_batch`'s `_stream_episodes` parser already maps that index → seed via `spec.batch[index-1]`, so it needs no change for correctness. The TUI (`tui/app.py`) updates the live per-seed table **by index/seed row** (episodes may finish out of order) and shows a completed-count rather than a monotonic "k/N". Up to P episodes appear in flight at once.

### Determinism
Each trajectory keeps its own HMAC seed, so parallel execution yields **bit-identical results** to the serial path. To keep P oversubscribed envs from thrashing shared math threads (and to protect that determinism), the arena image pins single-threaded BLAS (`OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` / `MKL_NUM_THREADS` / `NUMEXPR_NUM_THREADS` / `VECLIB_MAXIMUM_THREADS` = 1), matching the sibling's reproducibility posture (add if not already set).

### Error isolation
A worker exception / timeout / crash is caught and recorded as a failed `TrajectoryResult` (status `bot_error` / `trajectory_timeout`, progress 0.0) for that episode; the batch always completes and returns N results.

### Per-worker warm-up — explicitly *not* engineered
A worker process pays a one-time startup cost (imports, plus any lazy JIT / model-load the solution does — e.g. AutoAscend's numba). We deliberately build **no** cache or warm-once step, for two reasons: (1) it is a growing liability as evolved solutions accrete logic, and (2) a content-keyed cache is nearly useless under evolution — every candidate is different source, so it mostly misses. Within an eval the cost is already minimized for free (a pool worker warms once and amortizes over the episodes it runs); across evals (fresh containers) we accept cold start. The residual is simply one of the empirical factors folded into `--max-parallel-evals` — heavy warm-up → optimal setting is fewer parallel envs each amortizing over more episodes. Nothing solution-specific enters the design; the fan-out mechanism is solution-agnostic.

## 4. Blast radius

- `arena/run.py` — replace the serial loop with the `ProcessPoolExecutor` fan-out (submit-by-index, stream on completion, collect in order).
- `eval/runner.py` — thread `--max-parallel-evals` into the `docker run` command (the stream parser is already index-keyed).
- `tui/app.py` — key the live episode table by index/seed for out-of-order completion.
- `cli.py` — expose `--max-parallel-evals` (default 8), pass to `eval_batch` and the evolve loop's eval calls.
- `arena/Dockerfile` — pin single-threaded BLAS if not already.

The evolve loop, gate, brief, seeds, and hub are untouched.

## 5. Testing

**Unit (no Docker/NLE):**
- `arena/run.py` fan-out over a fake `run_trajectory`: N results returned in batch order, count == len(batch), and `P == min(--max-parallel-evals, len(batch))`.
- a `run_trajectory` that raises for one index → that episode becomes a failed `TrajectoryResult`, the batch still returns N results.
- `eval_batch` includes `--max-parallel-evals P` in the docker argv (fake runner asserts the command).
- out-of-order per-episode callbacks still resolve to the correct seed (index-keyed).

**Gated (`docker` + `nle` markers):**
- a real 4-episode batch at `--max-parallel-evals 4` returns results **identical** to the serial path (determinism), and completes faster (wall-clock assertion with margin).

## 6. Follow-ups (not this spec)
- Optional `--cpus` passthrough to bound the container.
- A `--max-parallel-evals` sweep to find this machine's throughput pareto point.
- Multi-container / cluster eval backend, and parallel-candidate search — separate specs.
