# MAP-Elites Evolve on the Identity Archive

**Status:** design / awaiting review
**Date:** 2026-08-26
**Author:** vkurenkov (with Claude)
**Builds on:** `main`'s landed leaderboard rework (`2026-08-26-leaderboard-rework-design.md`, "objectives as views on atoms") — the hub read side already exists; this design fills the archive those views read and makes the evolve loop true MAP-Elites over it.

Throughout, **`S`** is the objective's identity set — one identity, a role, or generalist (all 73).

## Problem

1. **Not a MAP-Elites *archive*.** Only wins register: the loop publishes a program only when it beats the objective's validation-gated scalar average, so every **specialist** the search evaluates — a program that improves one identity but not the average — is discarded, along with every runner-up. Wins are rare (zero across three recent monk runs), so the archive MAP-Elites depends on stays nearly empty.
2. **Not MAP-Elites *selection*.** The loop is a greedy per-island hill-climb on the scalar average, and `_reset_islands` (`harness/loop.py`) evicts the globally-worst — the opposite of illumination.
3. **Parallel publishing races.** `publish_solution` (`hubclient/publish.py`) pushes every program to the same default branch (`git push -u origin HEAD`); two concurrent runs hit a fast-forward collision and the loser silently becomes `local-only`.
4. **The atom model still carries `objective_digest`** — redundant now that `random`/`all` are being retired: once they're gone, every atom for identity F sits on F's canonical batch, so the identity is the key.

## Goal

Make the loop true MAP-Elites and fill the archive it runs on, on top of `main`'s identity-view hub:

- **Archive.** Register every program that completes its dev eval — improved or not — as per-identity atoms keyed by identity.
- **Reads.** Reuse `main`'s `board` / `aggregate_board` / `hacker_board`, rekeyed from `objective_digest` to `identity`; semantics unchanged.
- **Search.** Islands, the champion, and the average gate are removed; parent selection is a random cell in `S`.
- **Validation removed.** Cell insertion uses the dev (canonical-batch) score alone; held-out truth is the hub verification tier's job.

## Core model: atoms keyed by identity

The atoms table **is** the archive. Every view over it is a read-time aggregation `main` already computes:

- `aggregate_board` (`hub/views/boards.py`) — generalist / role coverage + mean, ranked coverage-first;
- `board` — the per-identity ranking;
- `hacker_board` (`hub/views/hackers.py`) — per-owner union of best-per-identity;

— each rekeyed from `objective_digest` to `identity`. A specialist is the degenerate `S = {F}` (the per-identity board); a generalist is coverage + mean over its `S`; a new objective is just a new identity set handed to the same views — no stored state per objective.

## Retire `random`/`all`, drop `objective_digest`

`random` is obsolete: `main` already unlisted `random`/`all` from the board picker, and `resolve_scope` (`boards.py`) only knows generalist / role / identity. Once they're retired, every atom for identity F is produced on F's canonical batch — so `identity` IS the canonical key and `objective_digest` is dead weight. Drop it:

- from the `Atom` model (`contracts/models.py`) and the `atoms` table — the column and its FK to `objectives`; the dedup key becomes `UNIQUE(solution_digest, identity, seed)`;
- from `evidence_to_atoms` (`hub/atoms.py`), `insert_atoms` / `iter_atoms` (`hub/store.py`), and `baseline_atoms`;
- with a migration collapsing any duplicate `(solution, identity, seed)` rows (keep the earliest).

**Rekey `main`'s views** — a mechanical swap: `iter_atoms(objective_digest=CATALOG[ident].digest(), tier=tier)` becomes `iter_atoms(identity=ident, tier=tier)` in `aggregate_board` and `hacker_board`, and the matching filter in `board` (whose functional-`all` breadth-rollup branch retires with `all`). Coverage / mean / ranking rules are untouched — `main`'s prototype-validated semantics are preserved, and the per-component comparability its docstrings guard (never mix another objective's seeds into an identity) holds *because* `random`/`all` are gone. The `objectives` catalog table stays — `selector.resolve` and the register ladder's `WrongBatch` check still read it, keyed by its own digest; atoms just stop referencing it.

## Publishing: one ref per run, after the dev eval

- **Publish every smoke-passing program after its dev eval** (`passes_gate` in `loop.py` stays the only pre-filter). Order matters: eval first, then push — a crashed eval must leave no orphan commit.
- **One git ref per run:** each program is a commit on `evo-harness-<HARNESS_VERSION>/<run-id>` in `<owner>/nethacker`. `HARNESS_VERSION` is a new dedicated constant (`"v1"`), separate from the package version, bumped only when harness semantics change incompatibly. Per-run refs fix the shared-default-branch race.
- **Idempotent:** identical content already no-ops (`git commit` → "nothing to commit"); keep that.
- **Manual submissions (`submit` / `register`) use harness-neutral refs** — the `evo-harness` namespace is this harness's own bookkeeping; the archive is producer-agnostic.

## Registration: server-side sliced, identity-keyed, self-reported

After the dev eval, push and register **every scored program** — improved or not — with `tier="self-reported"`.

- **Server-side slicing.** The register endpoint accepts one set-objective evidence and stores per-identity atoms keyed by identity — one hub call per program, not N. This kills the client-side per-identity loop `register_win_slices` (`harness/register.py`).
- The `register` ladder (`hub/validate.py`) is otherwise unchanged: `WrongBatch` still rejects evidence whose `(seed, character)` set differs from the objective's catalog batch — the anti-cherry-picking guard.

## Selection: MAP-Elites over the cells

Cells = identities; the per-identity archive is the population. Each iteration:

1. **Pick a random cell in `S`** (uniform; curiosity-weighted choice is deferred).
2. **Mutate that cell's elite.**
3. **Insert the child into every cell of `S` it improves** (best-per-cell). Specialists are kept and become future parents.

Cells are seeded from `main`'s per-identity elites (`read_elites` / `board(identity)`). **Islands are removed** — they were index-keyed replicas of the same scalar objective, and `_reset_islands` evicts the globally-worst, the opposite of keeping diverse cells. There is no champion and no average gate. A program's generalist score is a derived leaderboard view — `main`'s `aggregate_board` — never the selection driver.

## Validation removed

The second, held-out validation eval is dropped:

- **A held-out score can't join the single canonical leaderboard.** Different seeds mean a separate board — 30 evals per program plus double verifier effort — so a validation score could only ever stay local.
- **The real held-out check belongs at the hub's verification tier**, on secret seeds the submitter can't see or target; a local check on the harness's own visible seeds was always the weaker copy. Verification is separate and later — and now a real dependency, not a nice-to-have.
- **Cell insertion uses the dev (canonical-batch) score alone.** This does not touch the fitness metric.

## Tiers

Everything published here is `self-reported`. `verified` is a separate, earned tier minted only by hub verification (out of scope). Boards distinguish the two.

## Frozen-fitness compliance

`progression` is computed exactly as before. Only atom *keying* and *search control* change — which programs are kept and mutated — never what a program scores.

## TUI

The islands panel becomes a cell-archive view: per-cell elite score, `S` coverage (filled / total cells), and the cell the current iteration is mutating.

## Out of scope

- **Verification pipeline** (self-reported → verified).
- **Eval noise / 0.0-abort handling** (frozen fitness; untouched).
- **Curiosity-weighted cell selection** (uniform ships).

Dropping `objective_digest` IS in scope here.

## Open risks

1. **Fetch-by-SHA.** The hub `pull` path must fetch a specific commit SHA on a non-default branch; confirm, else add a targeted `git fetch <sha>`.
2. **The rekey touches freshly-landed views.** `aggregate_board` / `board` / `hacker_board` just landed on `main`; the swap is mechanical and their existing tests hold the semantics.
3. **Atom migration.** Dropping the column + FK and collapsing duplicates on live data.
4. **Volume / index.** Register-all multiplies atom growth and push rate; confirm SQLite + GitHub headroom, add an index plan.
5. **Trust / spam.** A self-reported firehose is the intended trade; boards are tier-filtered and verification stays selective.
6. **`random`/`all` consumers.** Confirm nothing still depends on them before retiring.

## Testing

- **Unit:** per-run-ref push is parallel-safe (two publishers, distinct refs, both succeed); register stores per-identity atoms for a program that improves no cell; server-side slicing stores, from one set-objective evidence, the same atoms N single-identity registrations would; the rekeyed `aggregate_board` / `hacker_board` reproduce their existing expectations on identity-keyed atoms; the migration collapses duplicates keeping the earliest.
- **Integration:** two concurrent runs publish + register with no collision and no lost registration; the loop picks a random cell from `S`, mutates its elite, and inserts a one-identity improver into that cell (retained, not discarded) — the MAP-Elites behavior this spec introduces.

## Components

- **hub** — atoms schema + migration (drop `objective_digest`; `UNIQUE(solution_digest, identity, seed)`); rekey `board` / `aggregate_board` / `hacker_board` by identity; server-side sliced register; retire `random`/`all`; clean per-identity elite read (`read_elites` sheds its cross-objective caveat).
- **harness** — new `HARNESS_VERSION` constant; `publish.py` per-run refs; `loop.py` → MAP-Elites (register-all, random-cell parent, islands + validation removed); `select.py` → cell seeding from hub elites (drop `select_parent` / `_coverage_gated_entries` / `influence_pool` island seeding).
- **tui** — islands panel → cell-archive view.
- **cli** — `submit` / `register` unchanged; the hub keys their evidence by identity.
