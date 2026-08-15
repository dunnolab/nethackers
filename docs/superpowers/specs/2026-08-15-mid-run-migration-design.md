# Mid-run elite migration (concurrent compounding) — Design

**Status:** approved design, pending implementation
**Date:** 2026-08-15
**Depends on:** SELECT-from-hub (`2026-08-14-select-from-hub-design.md`)

## Goal

A running `evolve` process adopts a strictly-better elite that *another* process
registered to the hub, **mid-run** — turning the hub into a live shared
blackboard so concurrent runs **compound** instead of running as isolated
islands.

## Background: today's behavior (the gap)

- SELECT happens **once, at run start** (`cli.py`, before `run_loop`).
- `run_loop` then mutates from its **own local `elite`** (`loop.py`,
  `copytree(elite.tree, …)`), and that `elite` only advances when **this
  process's own mutation wins** (`elite = EliteState(...)` after `register_win`).
- The loop **writes** wins to the hub but **never reads back**.

⇒ Two concurrent runs share nothing while in flight. Compounding is *sequential*
(each new run SELECTs the current best at startup), not *concurrent*.

## Design

### Trigger — per iteration, before mutating

At the top of each `for k` iteration (before the `copytree`), query the hub for
the objective's **top trusted elite** — *greedy* (max `score`), **not** the
`--select-k` softmax sample. Migrate iff **both**:

1. `top.solution_digest != elite.digest` — it's a genuinely different elite, **and**
2. `top.score > elite.dev_fitness` — strictly better on the hub's ranking metric.

Why this is **exact** (not approximate) for identity objectives: `evaluate`
returns `evidence.mean_progress` (`evaluate.py`), and `register_win` submits that
*same* dev evidence, so a solution's hub `score = AVG(progression)`
(`elites.py:_RANK_SQL`) is computed from the **identical atoms** — `top.score`
**equals** the elite's `dev_fitness`. The comparison `top.score >
elite.dev_fitness` is the same number on both sides, so there is no metric
skew. (Validation lives on distinct reserved seeds — `seeds.py`,
`validation_spec(start=1000)` — and is re-eval'd on migration; it never enters
the trigger.)

Why greedy + strict + digest-differ:
- **Greedy** (not sampled): migrations fire only on *real* registrations, so
  they stay rare (tied to the win rate — empirically hours apart), no churn.
- **Strict `>` + digest-differ**: the loop is **self-limiting / loop-free** —
  after migrating onto `X`, the local digest equals the hub top, so the trigger
  can't re-fire until some *newer, higher-scored* elite appears. Ties never
  migrate.

### Action — on migrate

1. **Resolve** the elite's tree via the local content-addressed cache / `pull`
   + digest integrity check (the *exact* resolution `select_parent` already
   does). **Any** failure (hub error, cache miss + pull failure, digest
   mismatch) ⇒ **keep the local elite, no migration.**
2. **Re-evaluate** the adopted tree on **dev + validation** (the same specs the
   cold-start uses) to compute `dev_fit, dev_ev, val_fit` *in this run's terms*,
   then `elite = EliteState(top.digest, tree, dev_fit, val_fit, dev_ev)`. This
   keeps the gate math (`child > elite.dev_fitness` and
   `> elite.validation_fitness`) correct after the parent changes, exactly as
   the cold-start establishes the seed's baseline.
3. **Surface it:** `report(...)`, an `_emit("migrated", …)` for the status bar,
   and an `on_log` line into the mutation-log tab
   (`migrated ← {owner}/{digest[:12]} score {s:.3f} > {dev:.3f}`).

Then the iteration proceeds normally — the `copytree` now copies the migrated
`elite.tree`, so the mutation is from the better parent.

### Deliberate splits

- **Run-start SELECT is unchanged** — still the `--select-k`/`--select-temp`
  softmax sample, for cross-run *diversity*. Mid-run migration is **greedy
  move-up** (exploit). Different jobs, different policies.
- **Cost:** one cheap hub query per iteration (a no-op comparison ~99% of the
  time); a dev+validation re-eval **only on an actual migration** (rare).

## Component changes

### `harness/select.py`

- **Extract** `_resolve(entry, store, fetch) -> tuple[Path, str] | None` — the
  cache-hit / pull + `store.save` integrity block (current `select_parent`
  step 4). `None` on miss+fail or digest mismatch.
- **Add** `top_trusted_elite(hub, objective, store, owner, *, fetch=pull_fetch)
  -> tuple[dict, Path] | None`: `hub.elites(objective)` (exception ⇒ `None`) →
  keep `_trusted(e, owner)` → **greedy** `max(..., key=score)` (no sampling) →
  `_resolve`. Returns `(entry, tree_path)` or `None`.
- `select_parent` keeps its behavior (run-start, sampled), refactored to call
  `_resolve` for its step 4.

### `harness/loop.py`

- **Factor** `_score_elite(tree_path, digest, *, label) -> EliteState`: the two
  `evaluate(...)` calls (dev → `dev_fit, dev_ev`; validation → `val_fit`)
  wrapped into an `EliteState`. Reused by **both** the cold-start and migration,
  so the re-eval path is identical to how the seed baseline is scored.
- **New param** `migrate: bool = True`.
- At the **top of the `for k` loop**, before `copytree`, when `migrate`: call the
  migration check. On a triggered migration, `_score_elite` the adopted tree,
  reassign `elite`, and emit. `EliteState` is **unchanged** (the trigger reuses
  `elite.dev_fitness`; no new field).

### `cli.py`

- Add `--no-migrate` (`action="store_true"`; default = migration **on**). Wire
  `migrate=not args.no_migrate` into `run_loop`. Record `"migrate": bool` in
  `run.json`.

### `tui/status.py`

- `format_status`: add a `"migrated"` phase →
  `line1 = f"↥ MIGRATED iter {k}/{n} ← {state['detail']}"` (detail = the
  `owner/digest` string). The re-eval progress rides the existing
  `evaluating-*` labels via `_episode_cb("{tag} · migrate-dev / -validation")`.

## Guards & edge cases (every one ⇒ keep local elite; never regress)

| Case | Behavior |
|---|---|
| Hub's top **is** my own just-registered win | same digest ⇒ no-op |
| Hub top not strictly better (`score <= elite.dev_fitness`) | no migration |
| Hub error / no trusted entries | `top_trusted_elite` → `None` ⇒ no migration |
| Cache miss + pull failure / digest mismatch | `_resolve` → `None` ⇒ no migration |
| `--no-migrate` | migration check skipped entirely |

## Testing

- **select.py**: `top_trusted_elite` returns the greedy top *trusted* entry (not
  a sample); drops untrusted; resolves a cache hit to `(entry, path)`;
  hub-exception / none-trusted / pull-fail / integrity-mismatch → `None`.
- **loop.py** (injected fake hub + fake evaluate): migrates when hub top is a
  different digest **and** strictly better → `elite` becomes the adopted digest
  with re-eval'd fitnesses; **no** migration when same digest, not-better, hub
  error, or `migrate=False`; own-win stays a no-op.
- **status.py**: the `"migrated"` phase renders its line.
- **cli.py**: `--no-migrate` flips the `migrate` kwarg; `run.json` records it.

## Non-goals (v1)

- **Post-re-eval re-check** — we trust the hub score for the decision; the
  digest guard makes an eval-noise "regression" minor and self-limiting. A
  strict re-check-after-re-eval is a possible refinement.
- **Migration rows in `metrics.jsonl`** — `report` + status + mutation-log line
  are enough observability for v1.
- **Sampled mid-run migration** — greedy only (per the migration-policy decision).
- **Functional objectives** (`random`/`all`) — the hub ranks *per identity* and
  returns a rank-major spread, so a single greedy "top score" isn't the
  objective's cross-identity weighted-sum fitness. Migration alignment there
  inherits the pre-existing "parked caveat" in `elites.py` (cross-objective
  means aren't apples-to-apples). Identity objectives are exact; functional-
  objective migration is a future refinement.
