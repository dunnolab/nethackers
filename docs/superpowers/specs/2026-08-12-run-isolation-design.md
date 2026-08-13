# Per-run isolation + record (wandb-style) — design

**Status:** Approved in brainstorming 2026-08-12; implementation on branch `m3/eval-throughput` (folds onto the parallel-eval + hermetic-operator line).

## 1. Problem

`nethackers evolve` reuses one stable workdir — `~/.nethackers/evolve/` with `trees/` (elite store) and `work/iter-N/` (worktrees) — for **every** run. Consequences:

- **Results conflate across runs.** The local elite `trees/` store accumulates wins from all runs (and branches), so an outcome can't be attributed to the run that produced it.
- **The operator's cwd is reused.** `work/iter-0` is the same path every run, and Claude Code keys per-directory memory by cwd path (`~/.claude/projects/<cwd-slug>/`), so a new run's agent recalls the previous run's memory (observed 2026-08-12).
- **Nothing is persisted.** Per-iteration dev/held-out fitness, tokens, and win/reject reasons stream only to stdout/TUI; once the terminal scrolls, a run's numbers are gone (this blocked recovering scores during M3 debugging).

## 2. Goal & scope

Each `evolve` invocation writes to its own run directory (wandb-style) holding that run's working dirs, elite store, config, and a per-iteration metrics log. Runs become isolated, reproducible, and comparable.

**In scope:** per-run directory (`work/` + `trees/`), a `run.json` config manifest, a `metrics.jsonl` per-iteration log, a `latest` symlink, `--run-name`.

**Out of scope (deferred):** continuing a run from a prior run's elite (`--from-run`), pruning old runs, a `nethackers runs` index/list command.

## 3. Design

### Layout
```
~/.nethackers/evolve/                 # --workdir root (unchanged default)
  runs/
    <run-id>/
      run.json          # static config, written once at start
      metrics.jsonl     # one line per iteration, appended live
      logs/iter-N.log   # raw coding-agent stream, per iteration
      work/iter-N/      # worktrees — a fresh cwd per run
      trees/<digest>/   # this run's local elite store (cold-starts from the seed)
    latest -> <run-id>/ # convenience symlink to the newest run
```
`--workdir` still defaults to `~/.nethackers/evolve`. The CLI computes `runs/<run-id>/` under it and derives the tree store (`<run-dir>/trees`) and worktree root (`<run-dir>/work`) — the two paths `run_loop` already accepts. Per-run `work/` + `trees/`; the **hub stays the shared cross-run archive** (`register_win` unchanged). Because each run's `work/iter-0` is now a fresh path, the Claude Code per-cwd memory starts empty — a second, structural defense against operator-memory recall, complementing the hermetic flags.

### Run-id
`run_id = now_utc.strftime("%Y%m%d-%H%M%S")`; with `--run-name X`, `run_id = f"{ts}-{slug(X)}"` where `slug` lowercases and maps runs of non-alphanumerics to a single `-`. On the rare same-second collision, append `-1`, `-2`, … until the directory does not exist. The CLI computes it (the loop already receives an injected `now`).

### run.json — static config, written once at run start
```json
{"run_id", "created_at" (ISO-8601 UTC), "git_sha", "objective", "seed",
 "operator", "iterations", "token_budget", "timeout", "heldout_n",
 "max_parallel_evals", "image"}
```
`git_sha` = `git rev-parse HEAD` of the nethackers repo, best-effort (`null` if not a git checkout) — pins which code produced the run.

### metrics.jsonl — one JSON object per line, appended live
One line per iteration, plus iteration 0 for the cold-start baseline:
```json
{"iteration", "outcome", "reason", "dev_fitness", "heldout_fitness",
 "tokens", "stopped_reason", "child_digest"}
```
- `outcome ∈ {baseline, registered, rejected, error}`; `reason` carries the reject/error detail (`no-dev-gain`, `no-heldout-gain`, `gate:<...>`, or the error string).
- Iteration 0's line records the seed's baseline dev + held-out fitness.
- Tail-able during a run.

### mutation logs — `logs/<tag>.log`
The coding-agent's raw per-line stream (its reasoning + tool calls + output) is persisted per iteration. `on_log(tag, line)` — already flowing to the TUI's mutation-log tab — is wrapped once in the CLI so it *also* appends each raw line to `runs/<id>/logs/<slug(tag)>.log` (tag `"iter K/N"` → `iter-k-n.log`). It composes with the TUI (render **and** persist); in non-TUI mode it persists instead of dropping. This matters more now: the hermetic `--no-session-persistence` flag means Claude Code no longer keeps its own transcript, so this file is the only lasting record of *how* a mutation was produced. New helper `runlog.append_log(run_dir, tag, line)`.

### latest symlink
`runs/latest` → the new run-id, repointed at each run start (replace any existing). Best-effort — skipped with a warning if the platform/filesystem rejects symlinks.

### Components
- **New `src/nethackers/harness/runlog.py`** — pure, testable helpers with no CLI/loop coupling: `run_id(now, name) -> str`, `write_run_config(run_dir, config: dict) -> None`, `append_metric(run_dir, record: dict) -> None`.
- **`src/nethackers/cli.py` (`evolve`)** — compute run-id + run dir, write `run.json`, repoint `latest`, set `tree_store = LocalTreeStore(run_dir/"trees")` and the loop's `workdir = run_dir/"work"`, add `--run-name`, and pass an `on_iteration` sink that appends to `metrics.jsonl`.
- **`src/nethackers/harness/loop.py` (`run_loop`)** — add `on_iteration: Callable[[IterationResult], None] = lambda _: None`, invoked once per iteration (after its result is appended) and once for the cold-start baseline (an iteration-0 record carrying the seed's dev/held). No other logic changes.

## 4. Testing (no Docker/NLE)

- `run_id`: timestamp format; `--run-name` slugified + appended; same-second collision appends `-N` (inject an existence check).
- `write_run_config` writes valid JSON with every field; `append_metric` appends exactly one JSON line per call (N calls → N lines, order preserved, each parses).
- `run_loop` fires `on_iteration` once per iteration + once for cold-start, with the right `outcome`/`reason`/fitness (fake operator + fake evaluate + fake gate).
- CLI `evolve`: with a fake `now` + tmp workdir, it creates `runs/<id>/`, writes `run.json` (correct fields), points tree-store/work under it, and repoints `latest`.

## 5. Migration

New runs use `runs/<id>/`; the old flat `~/.nethackers/evolve/{work,trees}` is left in place, ignored by new runs — no data migration. The stale operator-memory dirs under `~/.claude/projects/*evolve-work-*` are sidestepped by the fresh paths and are already inert under the hermetic flags.
