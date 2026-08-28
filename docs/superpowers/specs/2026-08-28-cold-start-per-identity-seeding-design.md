# Cheap Cold Start: Seed Each Cell on Its Own Identity

**Status:** design / awaiting review
**Date:** 2026-08-28
**Author:** vkurenkov (with Claude)
**Builds on:** the landed MAP-Elites archive (`2026-08-26-evolve-full-archive-design.md`). That design made the loop illuminate a per-identity `CellArchive` and seed its cells from the hub's per-identity elites; this design fixes the **cost** of that cold-start seeding, which is quadratic in the objective size and stalls warm-started set runs before iteration 1.

Throughout, **`S`** is the objective's identity set (size `N`); **`b`** is the per-identity batch size (`per_identity_size`, today 15); **`P`** is the number of *distinct* trusted champion programs the hub returns across `S`.

## Problem

Cold-start (`run_loop` in `harness/loop.py`) scores **every** starting program on the **full union** `dev` batch:

1. **Cost is `(1 + P)·N·b` episodes — quadratic in `S`.** The seed is scored once on the union (`loop.py:189`), then the overlay scores each distinct hub champion on the *same* union (`loop.py:203`, deduped by digest via the `scored` dict). `dev = build_union_spec(S)` is `N·b` episodes (`objectives.py`, `build_union_spec`). So a 73-identity generalist warm-started from ~45 distinct champions pays `46·73·15 ≈ 50k` episodes — roughly **46 iterations of eval compute before the first mutation**. Worked example (`N=90`, `P=45`): `60,750` overlay + `1,350` seed. The concrete symptom: a warm-started `mon` run (`N=6`) sits in cold-start with **zero `metrics.jsonl` lines** while it grinds `(1+P)` sequential union evals; at generalist scale it never starts.

2. **The full-union scoring is redundant at cold start.** The hub already assigns each identity its best program. Scoring champion `C` (the elite for identity `X`) on the other `N−1` identities cannot change the archive — each of those identities already holds an equal-or-better elite. The "insert into every cell it improves" illumination (`archive.insert`, `archive.py`) is worth paying per **novel child** during the loop; it buys nothing for programs the hub has already placed.

3. **The seed is scored on the union even where a champion overwrites it.** `evaluate(seed, dev, …)` runs unconditionally (`loop.py:189`), fills every cell, then champions overwrite the cells they beat. If champions cover all of `S`, autoascend becomes the elite of **no cell** — its `N·b` eval bought only the `base_dev` number and a discarded fill. ("Why re-evaluate autoascend when it isn't an elite?" — it currently isn't necessary.)

4. **`base_dev` leans on that seed union eval**, and the local seed eval under CPU contention is itself a known contamination source (`autoascend-baseline` timeout finding) — the weaker, contention-sensitive copy of a baseline the hub already owns authoritatively.

The pathology is **set** objectives (role / generalist). A singleton (`N=1`, `P≤1`) is already cheap and unaffected.

## Goal

- **Cold-start cost `≤ N·b` episodes** — one union batch *total*, independent of `P`.
- **Each cell seeded and freshly measured on its own identity's canonical batch** — no cross-identity re-scoring of programs the hub already placed.
- **Per-iteration loop unchanged.** The full-union child eval + insert-all illumination stays exactly as landed; only cold-start changes.

## Design: partition `S` by champion, evaluate each owner once on its own identities

The champions **partition** `S`: `per_identity_elites` (`select.py`) returns one champion per identity, and distinct champions cover disjoint identity sets. Exploit that:

1. **Fetch** `elites = per_identity_elites(hub, S, owner)` (skip entirely when `--from-seed`).
2. **Group identities by champion digest.** Champion `C` owns `I_C ⊆ S`; identities with no trusted / resolvable champion form `I_seed`. `{I_C} ∪ {I_seed}` partitions `S`.
3. **Evaluate each champion once on its own sub-union.** `evaluate(C, build_union_spec(I_C), …)` — `|I_C|·b` episodes — then `archive.insert(C, tree, ev)` slots it into exactly the cells of `I_C` (per-identity means over `I_C` only).
4. **Evaluate the seed once on `I_seed`.** `evaluate(seed, build_union_spec(I_seed), …)`; insert. Skipped when `I_seed` is empty (every identity has a champion).
5. **`base_dev` = mean of the cold-start frontier's per-cell scores** (see D1).

Because the sub-unions partition `S`, every identity is scored **exactly once**, under whichever program owns it → total `N·b`, whatever `P` is. For `N=90, P=45` this is `1,350` episodes, a **~46× reduction** from `62,100`.

`build_union_spec(I_C)` concatenates each member's `CATALOG[ident].batch` (`objectives.py`), so a champion's per-identity means stay structurally identical to the union case — only the *unused* identities are dropped from its eval.

## Decisions

- **D1 — `base_dev` is the cold-start frontier mean, not the seed's union mean.** Dropping the seed union eval removes the old definition. Redefine `base_dev = mean(c.score for c in archive.cells.values())` after cold-start — the warm-started frontier the run actually departs from, computed with no extra eval. "How do we compare to AutoAscend?" is answered by the hub's isolated `baseline_atoms` (the authoritative, uncontended baseline), never a local seed eval. This *changes the iteration-0 baseline number's meaning* and is deliberate.
- **D2 — a warm-started cell's `dev_evidence` is its own identity's `b`-episode results, not the union.** The first brief for that cell (`build_brief`, `loop.py`) then shows exactly that identity's performance — the signal a single-cell mutation needs. Cross-identity context in the *first* brief shrinks versus today; it returns naturally as the loop's full-union children land. Accepted tradeoff.
- **D3 — drop the seed-vs-champion guard** (`entry["score"] <= archive.cell(ident).score`, `loop.py:199`). It compares the hub's reported champion score against a *freshly measured seed* — the very eval we're deleting. Each cell is now simply owned by its champion (or the seed where none), measured fresh on its own identity. Risk: a champion that would underperform the seed on its identity is still adopted — but it is the hub's declared best there, and the loop improves the cell regardless; measuring both everywhere is the `2·N·b` we're avoiding.
- **D4 — iteration-0 `causes` / `dev_fitness` come from the assembled frontier evidence**, not `seed_ev.results` (`loop.py:210`), since the seed may cover only `I_seed` (or nothing).

## Alternatives considered

- **Trust the hub's stored scores; no re-eval (0 episodes).** Populate cells straight from `entry["score"]`, eval lazily on first mutation. Rejected: hub scores are from prior runs on possibly different conditions (and the timeout-contamination finding shows local scores drift under load), so cells would start on non-comparable numbers, and the first brief would have no base evidence. Re-measuring each cell's owner **once on its own identity** is the cheap, honest middle ground — same `N·b` a single seed eval already cost, and every cell's score is a fresh local measurement.
- **Keep the union eval but parallelize / cap it.** Does not fix the `O(P·N)` scaling — `P` full unions of *work* remain; at generalist scale it's still ~50k episodes. Rejected.

## What stays unchanged

- **Per-iteration mutate → smoke gate → full-union dev eval (`loop.py:297`) → insert-all → register.** Illumination is paid per novel child, where it belongs.
- **`--from-seed`** still means "ignore the hub." It now seeds every cell from the seed in one union eval (`I_seed = S`); `base_dev` = frontier mean = seed's union mean. Behavior-consistent.
- **`CellArchive` / `archive.insert`** — already keyed on whatever identities appear in the evidence; per-identity evidence simply makes each insert touch one cell. No change.
- **`per_identity_elites` (`select.py`), the hub, registration, publishing** — untouched. This is a harness cold-start change only.

## Out of scope

- The per-iteration full-union child eval (`N·b` per mutation) — that is the illumination budget; a partial-union or curiosity-weighted child eval is a separate, deferred question.
- Verification tier and the fitness metric — frozen; untouched.

## Open risks

1. **Coverage semantics.** Champion-seeded cells start with a non-seed digest, so `coverage()` (`archive.py`) counts them "advanced" from iteration 0 — identical to today's overlay, but confirm the monitor renders a warm start as non-zero coverage sensibly.
2. **`base_dev` consumers.** The redefinition is user-visible in the monitor and the iteration-0 metric; the TUI formatters reading `baseline_dev` must tolerate the new meaning (`tui/status.py`, `run.py`).
3. **Resolve failures.** A champion whose tree fails to resolve (`_resolve → None`, `select.py`) must drop its identity into `I_seed`, not leave the cell empty — the partition must be built from *resolved* champions only.
4. **Empty `I_seed`.** When every identity has a champion, no seed eval runs; ensure `base_dev`, iteration-0 `causes`, and `archive.mark_seed` still have well-defined inputs (mark the seed digest without an eval; frontier mean over champion cells).

## Testing

- **Unit:** cold-start over a 3-identity `S` with two distinct champions (one owning 2 identities) issues exactly **two** champion evals — each on its owner's sub-union batch, never the full union — plus at most one seed eval over the championless remainder; total episodes `= N·b`. The 2-identity champion is evaluated once on its 2-identity sub-union and inserted into both cells. `base_dev` equals the mean of the cold-start cell scores. `--from-seed` seeds all cells from the seed in one union eval and never calls the hub. A champion that fails to resolve falls into the seed set.
- **Integration:** a warm-started `mon` run reaches iteration 1 after `N·b` cold-start episodes (not `(1+P)·N·b`) and writes its iteration-0 baseline; each cell's initial elite is its hub champion; the first brief for a cell shows that identity's evidence.

## Components

- **harness** — `loop.py` cold-start rewrite: partition `S` by resolved champion; one sub-union eval per champion + one over `I_seed`; `base_dev` = frontier mean; iteration-0 `causes`/`dev_fitness` from frontier evidence; drop the seed-vs-champion guard. Reuse `build_union_spec` for sub-unions (`objectives.py` / `seeds.py` — likely no change). `per_identity_elites` (`select.py`) unchanged.
- **tui** — tolerate the redefined `baseline_dev`; optionally show cells filling as champions land during cold-start (`tui/run.py`, `status.py`).
