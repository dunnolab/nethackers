# Evolve Harness — Union Cell + Brief Fix

**Status:** Design — ready for review
**Date:** 2026-09-02
**Extends:** `2026-08-24-evolve-exploration-overhaul-design.md` (the two-level sandbox/task framing) and `2026-08-26-evolve-full-archive-design.md` (MAP-Elites over the identity archive — the cell model this doc generalizes). Both are the foundation, unchanged except where noted.
**Diagnosis behind it:** a four-agent deep dive on runs `20260902-013111` (val) and `20260901-015416` (mon), 2026-09-02. See Appendix A for the grounded findings; the short version is in §1.

**How to read this.** Audience is whoever implements it. Read §2 (mental model) first — the harness/hub split is the invariant the whole change defends. §3 is the glossary; §4 is the decisions ledger (the *why*); §5 is the design; §7 is the invariants the change must preserve. Scope is deliberately small (§6 boundary, §10 out-of-scope) — this fixes the two *confirmed* harness-layer pathologies and nothing else.

---

## 1. Problem

The evolve loop stops registering new candidates a few dozen iterations into a run (val: last registration at iter 23 of 37; the registration hazard decays from ~0.22 to ~0.04 across a run). The four-agent dive separated two *layers* of cause and found that only some belong to the harness:

**Confirmed harness-layer pathologies (this doc fixes these):**

1. **Union wins are discarded.** Registration accepts a child only if it strictly beats an incumbent on ≥1 *per-identity* cell (`harness/archive.py`, `harness/loop.py`). A child that raises the *overall* score but wins no single identity is rejected. In the val run, iter-12's child had the run's best overall score (union ≈ 0.148) and was thrown away. The objective the brief states ("raise the average") is not the objective the archive rewards (per-cell max).

2. **The brief mis-targets.** The parent is a *random* cell's elite, but the brief tells the mutator to fix that parent's *weakest* identity (`harness/brief.py`) — which is almost always a cell already held higher by a different specialist. The edit can't beat that specialist's mean and leaves the parent's own (strong) cell untouched (exact tie → rejected), so genuine work registers nothing. The brief also falsely claims grading happens "on held-out seeds you'll never see."

**Not a harness problem (explicitly out of scope — the hub validator's job):** whether a training-block gain *generalizes* to unseen seeds. The dev eval is deterministic on a fixed public seed block, so a mean gain there is *exact*, not noisy — the harness is right to compare plain means and needs no statistical test, seed rotation, or held-out gate. The generalization gap (the "SE ≈ 0.03" spread of the 15-seed mean around the all-seed mean) is real but is measured by the **hub validator on unseen seeds**, not defended against inside the harness. See §2.

## 2. Mental model (read first)

**Two layers, never mixed.**

| | **Harness** (this doc) | **Hub validator** (separate infra, not built here) |
|---|---|---|
| Job | climb hard on the fixed **public** dev seed block | check **generalization** on unseen seeds |
| Eval | deterministic → a mean gain is **exact** | the transfer test |
| Held-out? | **never** enters the harness | this layer *is* the unseen-seed check |
| Noise guards? | none needed (deterministic, exact on the block) | n/a — it *is* the guard, one layer up |

Because the mutator edits **general policy code** (not seed-indexed lookup tables), a gain on the concrete dungeons of the public block is structurally likely to be a general skill gain. So the harness should be allowed to climb the public block aggressively; the hub validator is what later filters for transfer. Building generalization defenses (rotation, accumulation, min-Δ, significance tests) *into the harness* is a layer error and is rejected (§4 D5).

**The archive is a set of cells; a cell is a target the search climbs.** Today every cell is one identity. This doc adds one more cell — the **union** — whose score is the overall mean the objective already computes. Registration and selection then treat the union like any other cell. That is the whole change, plus an honest brief.

## 3. Glossary

- **cell** — a target in the archive with a single **elite** (the best program found for it) and that elite's **score**. Today: one per identity. After this doc: the identities **plus** the union.
- **scorer** — the function that turns a child's episodes into a cell's score. A per-identity cell averages *that identity's* episodes; the **union cell** averages *all* episodes.
- **union score** — the mean over the whole union batch of episodes. This is **identical to the existing `dev_fitness`** (`harness/evaluate.py`) — no new metric is introduced.
- **incumbent** — the current elite of a cell; the number a child must strictly beat to take that cell.
- **registration** — a child is *registered* when it strictly improves ≥1 cell. After this doc, "≥1 cell" **includes the union cell**.
- **brief** — the task text handed to the operator (mutator) for one iteration (`harness/brief.py`).
- **dev block** — the fixed set of public seeds (trajectory ids 0–14 per identity) the harness evaluates on. Deterministic.
- **operator** — the coding-agent CLI a run wraps (`codex`/`claude`).

## 4. Decisions ledger

- **D1 — Union is a first-class cell, not a special case.** Model a cell as *(key, scorer)*; the union cell is just the cell whose scorer averages all episodes. Registration and selection stay single code paths. *Why:* smallest change, no branching logic, and the union score already exists as `dev_fitness`.
- **D2 — Registration credits a union win.** `registered ⇔ ≥1 cell improved, union included`. *Why:* directly recovers iter-12-shaped children (best overall, no single-cell win) that are currently discarded — the confirmed pathology #1.
- **D3 — Union-best is samplable as a parent, weighted 2× an identity.** *Why:* the overall objective should be a launch point, and the user wants a mild preference for it. Concretely, with N identities the draw weights are `1` per identity and `2` for the union (val, N=3: union 40%, each identity 20%).
- **D4 — Brief targets a winnable cell, honestly.** Drop the "improve the weakest identity" directive and the false held-out claim. Tell the mutator which cell it was sampled from, show the full scoreboard, and set the goal as "beat this cell *or* raise the union." *Why:* removes the mis-targeting (pathology #2) and stops lying to the operator; a concrete cell target avoids the "improve the average" vagueness the user flagged.
- **D5 — No generalization machinery in the harness.** No held-out gate, no seed rotation, no evaluation accumulation, no minimum-Δ threshold, no significance test. *Why:* the dev eval is deterministic and exact on the block; transfer is the hub validator's concern (§2). These were considered and rejected as layer errors.
- **D6 — Union stays harness-local; no hub schema change.** The union cell drives in-run selection and acceptance only. Union-only winners publish to the hub as ordinary programs with their per-identity atoms (the hub can derive union from atoms). *Why:* keeps the change inside the harness, avoids a hub catalog change (and the DB wipe that a batch/catalog change would force).

## 5. Design

### 5.1 Cell = (key, scorer); add the union cell
Generalize the archive so a cell carries a **scorer** over a child's episode results:
- per-identity cell → mean of that identity's episodes (today's behavior),
- **union cell** → mean of all episodes (≡ `dev_fitness`).

Insert logic is unchanged in shape: a child takes a cell iff its score under that cell's scorer **strictly exceeds** the cell's incumbent (deterministic strict `>`, no margin). The union cell only seeds on **full-coverage** evidence — results spanning every identity in one eval. Under the N·b cold start (§Appendix A), each champion is re-scored only on the identities *it* owns, a sub-union slice, so cold start seeds the union only in the special case where one champion sweeps every identity; otherwise the union stays empty through cold start and seeds on iteration 1's first full dev eval instead (which always spans the whole union).

### 5.2 Registration includes the union
`registered` becomes "child strictly improved ≥1 cell, the union cell included." A child may now register by (a) winning an identity cell, (b) winning the union cell, or (c) both. Case (b) is the iter-12 recovery. Publishing to the hub happens on registration as today — a union-only winner is published as an ordinary program + atoms (D6).

### 5.3 Selection samples the union
Parent selection draws a cell from `{identity₁ … identity_N, union}` with weights `1` per identity and `2` for the union (D3), then copies that cell's elite as the parent. This is the only selection change; single-elite-per-cell and the rest of the loop are untouched (population/curiosity/islands are deferred — §10).

### 5.4 Brief rewrite
Remove from `harness/brief.py`:
- the "improve the parent's weakest identity" directive,
- the "scored on held-out seeds you'll never see" claim.

Replace with:
- **Provenance:** "This program is the current best on **⟨cell⟩** (score X)." For a union parent: "…the current best overall (union X)."
- **Scoreboard:** the per-identity scores **and** the union score, so the mutator can see where the headroom is.
- **Goal:** "Improve **⟨cell⟩** above its current best, *or* raise the overall (union) score — a win on either is accepted."
- **Honest grading:** "You're graded on the fixed public dungeon seeds shown here. Prefer **general** NetHack improvements over seed-specific ones — hacks tuned to these particular dungeons won't hold up." (States only what is true now; it must not assert a held-out re-check, which is future hub-side infra, not part of the harness.)

The exact wording is intended to be iterated after first review (§11).

## 6. Scope boundary

This change lives **entirely in the harness** and touches what it **registers** and **samples from**. It does **not**: add a hub cell type or change any hub schema/catalog; change the objective's batch or seed set; touch the arena/mutator images or the deterministic eval; or add any unseen-seed/validation logic. Because the objective catalog is unchanged, **no hub DB wipe is required**.

## 7. Invariants (self-consistency contract — check changes against these)

- **I1.** The harness evaluates only on the fixed **public** dev block; it never reads unseen/held-out seeds.
- **I2.** Acceptance is deterministic strict `>` on a cell's mean over the public block — no rotation, no accumulation, no minimum-Δ, no statistical test.
- **I3.** Union score ≡ `dev_fitness` (the pooled union mean). There is exactly one overall metric; the union cell does not introduce a parallel one.
- **I4.** `registered ⇔ ≥1 cell (identity or union) strictly improved`.
- **I5.** The union cell is **harness-local**: no hub schema/catalog change; union winners publish as ordinary programs + per-identity atoms.
- **I6.** The brief never claims held-out grading and never directs "improve the weakest identity."
- **I7.** Parent selection draws from `{identities, union}` with the union weighted `2` to each identity's `1`.

## 8. Testing

Unit tests against the archive + loop, following the existing harness test patterns:

- **Archive / registration:** an identity-only win registers (unchanged); a **union-only** win registers (new); an **iter-12-shaped** child (best union, no identity cell) registers into the union (the regression this doc exists to fix); a child that beats nothing is rejected; a tie (exact-equal) does **not** register (strict `>`).
- **Union score:** the union cell's score for a child equals `dev_fitness` for the same episodes (I3).
- **Selection:** the parent draw includes the union cell; over many draws the union share matches the `2 : 1` weighting (I7).
- **Brief:** the rendered brief contains the provenance + scoreboard + dual goal and honest grading line, and contains **none** of the removed strings ("weakest", "held-out seeds you'll never see").
- **Cold start:** the union cell seeds only on full-coverage evidence — a champion that sweeps every identity, or otherwise iteration 1's first full dev eval; it does **not** seed from a per-identity champion's own sub-union slice.

## 9. Rollout / build order

Harness-only; no hub deploy, no image rebuild (the harness runs from source; arena/mutator unchanged). Suggested order: (1) cell-scorer generalization + union cell + tests; (2) registration includes union + tests; (3) selection weighting + tests; (4) brief rewrite + tests. A CLI release for pip users is optional and can follow once validated locally.

## 10. Out of scope (deferred)

- **Selection diversity (scope B):** fitness/curiosity-weighted sampling, islands (extends the 2026-08-24 spec).
- **Richer search (scope C):** multi-exemplar prompts (mutator sees several elites, not one — today `influences=[]`), top-m population per cell.
- **Breadth heuristic:** preferring children that win on *more* dungeons as a public-seed proxy for generality.
- **Hub validator:** unseen-seed validation of registered programs — the entire generalization layer (§2). Held-out lives here, never in the harness.
- **Rejected outright (layer errors, D5):** seed rotation, evaluation accumulation, minimum-Δ, statistical significance gates.

## 11. Open decisions (to iterate after review)

- **Brief wording (§5.4).** Written to my best judgment; expected to be tuned once we see the operator's behavior on a few iterations.

---

## Appendix A — Verified codebase facts (re-confirm exact line numbers at implementation)

From the 2026-09-02 diagnostic pass. Locations are the sites to change; re-verify the precise lines when implementing.

- **Cell model / insert:** `harness/archive.py` — one cell per identity; insert iff the child's per-identity mean strictly `>` the incumbent.
- **Registration / selection / brief wiring:** `harness/loop.py` — `registered = ≥1 cell improved`; parent = `rng.choice(identities)` copying that cell's single elite; brief assembly; cold-start champions re-scored in-run on the dev block.
- **Brief text:** `harness/brief.py` — "improve the weakest identity first" directive; "raise the average" goal (mismatched with per-cell-max acceptance); false "scored on held-out seeds you'll never see" claim.
- **Objective / cells / batch:** `hub/selector.py` (identity → cells; "val" → 3), `hub/objectives.py` (15 fixed trajectory ids per identity; the union batch already exists here).
- **Fitness:** `harness/evaluate.py` → `contracts/models.py` — `dev_fitness` = plain mean over the union batch; milestone table in `arena/progress.py`; bot failures score 0.
- **Determinism:** dev eval is deterministic on a fixed seed (≈273/300 cross-run episodes bit-identical); the union/"SE ≈ 0.03" is across-seed (generalization) spread, not measurement noise.

## Appendix B — Pre-existing defects surfaced (fix candidates, mostly out of scope)

Recorded so they aren't lost; only the brief items in §5.4 are fixed here.

- **`arena/run.py` ProcessPoolExecutor shutdown deadlock.** On interrupt, `with executor_factory(...) as ex:` → `__exit__` → `shutdown(wait=True)` blocks forever joining a wedged worker, leaving an orphaned `--network none` eval container running for hours at 0% CPU (observed: 15h). Fix candidate: bounded, cancel-on-interrupt teardown.
- **Concurrency contention zeros episodes.** Under multiple concurrent runs, episodes have been observed collapsing to 0.0 (≤6 turns, "completed"), ≈ −0.05/cell — a real artifact under parallelism (see the Aug-26 baseline-contamination finding). Fix candidate: per-host eval serialization/lock.
- **Stale stored incumbent score across image/date drift.** Pulled champions re-scored ~0.0012–0.0013 below their stored hub value; `evaluator_image` is the mutable tag for all atoms. Minor cross-run comparability issue.
- **Local lineage unjoinable.** Store manifests carry empty `parents`/`influences`; lineage is injected only into the hub payload (`register.py`). Ties to the harness-run-records-schema work.
