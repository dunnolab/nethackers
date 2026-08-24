# Evolve Exploration Overhaul — Design

**Goal:** make the evolve loop actually keep improving past its early plateau, by
fixing three interlocking weaknesses in one unit: an impoverished mutator brief,
a single-lineage search with no diversity, and a parent selector that discards
specialists.

**Status:** design (brainstormed). Next step after review: implementation plan.

---

## 1. Motivation (grounded in real runs)

Two live runs converged on the same failure mode:

- **`val-dwa-law-fem` (single identity):** climbed 0.111 → 0.167 in ~6 iterations,
  then ~15 straight rejections. One lineage, hill-climbing one elite, stuck.
- **`mon` (Monk role, a *set* objective):** **16/16 rejected**, glued to the seed
  (dev ~0.110). We pulled the actual brief it fed the mutator and found it is
  starving the model of information:
  - It **never says this is NetHack** or what "progression" means — the *set*
    branch of `build_brief` opens *"a set of 6 builds … raise average
    progression across these character builds"*, with one passing "NLE". The
    single-identity branch at least says *"NetHack bot … BALROG-style milestone
    metric"* and includes an **outcome tally** (`died×N, starved×N…`); the set
    branch **drops both**.
  - It propagates **one aggregate number per build** and nothing else. The
    eval's rich per-episode signal (how the game ended, dungeon depth, milestone
    reached, death cause) is thrown away.
  - There is **zero memory across attempts**: every mutation is a fresh agent
    that never hears what the last N tries changed or why they were rejected, so
    it re-treads the same ground.

There is also a known selection gap (issue #12): the parent selector is
**coverage-gated** — a program can only parent an objective `S` if it has
results for *all* of `S`. Correct for the leaderboard, wrong for *influence*: a
run over a role whose specialists each cover one build gets an empty pool and
cold-starts from the seed, discarding every specialist.

**Root reads:** the mutator is briefed nearly blind, the search has no
diversity to escape a local optimum, and good partial-coverage parents are
thrown away. This unit addresses all three.

## 2. The two-level model (the key framing)

Diversity lives at **two independent levels**, and keeping them separate is what
makes the design simple.

- **Global — the hub.** A shared, persistent *registry*, not a search:
  registered solutions (`repo@commit` + per-identity atoms) and the per-identity
  elite pools. Cross-user, cross-run. The per-identity elite pools **are** the
  global quality-diversity archive (issue #12's influence pool). The hub does
  not run evolution; it stores and ranks.
- **Local — islands.** A per-run, in-process, ephemeral diversity mechanism: K
  parallel lineages the loop keeps so a single run does not collapse to one
  hill-climb. Gone when the run ends.

They touch at exactly two points:
1. **Hub → islands (read):** the influence pool (§5) seeds the *initial* islands
   with diverse specialists, and may *occasionally* inject one cross-run
   specialist at a reset. It is **not** the per-reset reseed source — resets
   reseed from diverse *local* survivors (§4), so the hub read feeds cross-run
   diversity in without homogenizing the run onto the single global best.
2. **Islands → hub (write):** any island win that beats the objective's
   registered elite publishes + registers, exactly as today. This updates the
   global registry; it does **not** feed back into the run's other islands
   except through the secondary, diverse read above.

The loop of loops: *hub seeds a run → islands explore diversely & locally → a
win registers → the hub's pool improves → the next run seeds from a better hub.*

Non-goal (explicitly out): moving the population onto the hub (a shared,
novelty-ranked per-identity *population* instead of a single elite). More
powerful, much bigger hub change; deferred.

## 3. Component A — Brief overhaul (build first)

Highest leverage and cheapest: the current brief is actively starving the
mutator. `src/nethackers/harness/brief.py`.

**A1. A shared NetHack preamble, in *both* branches.** One short block anchoring
the task: this is NetHack (played via NLE); the metric is a BALROG-style
progression score that starts near 0 and rises as the bot gets deeper/further
into the game (the exact milestone definition is the evaluator's — the preamble
sources its wording from there rather than asserting a fixed ceiling); the goal
is to descend, survive, and progress. Stated once, plainly, in both the single
and set branches. No strategy spoilers (the mutator is a capable coding agent) —
just the frame and what the number means.

**A2. Propagate what the eval saw, not just a scalar.** From
`parent_evidence.results` (which already carries per-episode `end_status`,
`max_depth`, `milestone`, `progress`), render for the parent:
- the **outcome tally** (`died×N, starved×N, …`) — in *both* branches (the set
  branch currently omits it);
- **where it stalls**: the typical deepest milestone / depth reached, and the
  dominant death cause — overall, and per-build for a set (the per-build line
  today shows only the score).

**A3. Failure feedback across attempts — data, never a directive.** The loop
already asks the mutator to leave a `# hypothesis: …` comment at each edit. On a
rejected iteration, record `(hypothesis, measured-result, what-changed)`.
`build_brief` gains `recent_attempts` and renders them **neutrally** — no "avoid
this" — so the mutator (a capable coding agent) judges for itself whether a
failed hypothesis was a *wrong direction* or a *right direction implemented
badly*, and can re-implement it better, generalize it, or drop it. Because each
mutation starts fresh from the island champion (not from the rejected attempt),
re-attempting a good idea is natural.

Each recent attempt carries:
- the **hypothesis** (the mutator's own words),
- the **measured result** — dev/validation deltas — with the **overfit case
  (dev↑, validation↓) called out explicitly** as the sharpest signal ("helped
  training, didn't generalize");
- a **bounded diff of what it changed** for the last 1–2 attempts (older ones:
  hypothesis + result only). The diff is what lets the model tell a buggy
  implementation from a bad idea from the outside; it is size-capped to avoid
  prompt bloat.

Rationale for *no ban-list*: a rejected hypothesis may be a good direction whose
implementation was poor; a "don't repeat X" list would suppress it. Reporting
the attempt + its result, and trusting the mutator, preserves that.

Interface: `build_brief(..., recent_attempts=None)` where an attempt is
`(hypothesis: str, result: str, diff: str | None)`; the NetHack preamble is a
module constant reused by both branches; A2 helpers may live in `aggregate.py`
(pure functions over `Evidence.results`).

## 4. Component B — Islands (local diversity)

`src/nethackers/harness/loop.py`. Replace the single `elite` with **K islands**.

**State.** `islands: list[EliteState]` of length K (each: its own champion tree +
dev/validation fitness + dev evidence), plus a round-robin cursor and, per
island, a short `recent_attempts` history for Component A3.

**Seeding.** Initialize each island from the influence pool (§5) if the hub has
trusted entries for `S`, else from the cold-start seed. Distinct seeds where the
pool allows; the seed otherwise.

**Iteration (round-robin).** Iteration *i* works island `i mod K`: take that
island's champion as the parent, build its brief (with that island's
`recent_attempts`), mutate, run the existing gates (smoke → dev → validation).
If the child beats *that island's* champion it replaces it; otherwise append the
(hypothesis, reason) to that island's history. Islands are otherwise **isolated**
— a win in one never touches another. Isolation is the diversity.

**Reset (the plateau-breaker) — reseed from *diverse local survivors*, never
"the single best".** Every **T** iterations: rank islands by champion fitness,
**kill only the bottom half**; leave the **survivors untouched** (they keep
their distinct champions — that is where diversity is preserved). Reseed each
killed island from a **top-k-sampled *surviving island*** (temperature > 0, so
not always #1), clear its `recent_attempts`, and let it re-diverge by
independent mutation. This defunds stuck lineages and spreads winners *without*
collapsing the population onto one lineage — the pull toward winners
(exploitation) is balanced by reseed-from-diverse-survivors + re-divergence
(exploration).

The **hub influence pool is a *secondary* seed source, not the reset default**:
it seeds the *initial* islands (see Seeding) and may *occasionally* inject one
cross-run specialist at reset (e.g. one killed slot per reset), so a run can
adopt another run's/user's win without homogenizing. This is what **subsumes
today's mid-run `migrate`** — but as an occasional diversity injection, not an
every-cycle "adopt the single global best". (Note: for a single-identity run the
hub pool is thin early on, so local survivor diversity carries exploration;
that is precisely why islands exist on top of the hub.)

**Registration.** Unchanged: any island win that beats the objective's
registered elite publishes + registers (per-identity slices for a set). The
board stays "best across everything."

**Isolation, deliberately.** No cross-island references in v1 (pure FunSearch
isolation is simplest and proven). "Crossover-by-reference" — showing the
mutator a champion from another island — is a noted extension, not in scope.

## 5. Component C — Coverage-aware influence pool (issue #12)

`src/nethackers/harness/select.py`. Add influence selection alongside the
existing coverage-gated path (which stays, unchanged, for the *elites*
leaderboard).

**Pool.** For an objective `S` (its identities), the influence pool is the
**union** of each S-identity's per-identity elite pool — `hub.elites(ident)` for
each `ident ∈ S`, keep trusted (existing `_trusted`), take the union rather than
the intersection. A program appears once per identity-column where it is an
elite, carrying that column's score; a full-`S` program tops many columns
(sampled often), a single-build specialist appears in one (small but nonzero).
Coverage-weighting is emergent — no imputation, no magic weight.

**Sampling.** Reuse the existing `_sample` (trusted → top-k → temperature) over
the union pool. New entry point `influence_pool(hub, identities, owner, *,
fetch)` and a `sample_seeds(..., n)` that resolves *n* island seeds (via the
existing `_resolve`/cache/pull), falling back to the cold-start seed on hub
error / empty pool / unresolvable bytes.

**Brief hook (A2 tie-in).** A sampled specialist knows *which* identity it
excels at, so the brief can say "this parent is strong at `mon-hum-cha-mal` —
generalize it," closing the loop between selection and the brief.

The leaderboard read (coverage-gated `elites(S)`) is untouched. No hub
schema/endpoint change — the union is computed client-side from the same
per-identity `hub.elites(ident)` queries the coverage-gated path already makes.

## 6. Parameters (defaults + rationale; confirm in review)

- **K (islands) = 4.** FunSearch uses ~10, but its iterations are cheap; ours are
  minutes (real mutation + Docker eval), and round-robin means K× wall-clock per
  pass. 4 gives real diversity at tolerable cost. Configurable.
- **T (reset period) = 4·K iterations** (≈ 4 mutations per island between
  resets). Enough to diverge before culling; frequent enough to defund the
  stuck. Configurable.
- **top-k / temperature:** reuse `select.py`'s existing defaults.
- **1 elite per island** (not a per-island sub-population). Minimal; per-island
  pools are a fast-follow.

## 7. Error handling

- **Hub down / empty influence pool / unresolvable bytes:** seed/reseed from the
  cold-start seed (the pool functions never raise — same contract as today's
  `top_trusted_elite`).
- **Operator error on an island iteration:** the existing circuit-breaker
  applies; that island simply gets no new champion this turn (no cross-island
  effect).
- **Missing per-episode fields (older evidence):** A2 renders only what is
  present (tally always available; depth/milestone/death-cause best-effort).

## 8. Testing approach

All units stay pure/injectable (hub, rng, fetch, eval `runner` already injected).

- **Brief:** golden-ish assertions that both branches contain the NetHack frame
  + outcome tally; that `recent_attempts` renders the "don't repeat" list; that
  a set brief includes per-build stall detail. No LLM involved.
- **Islands:** with a fake improving operator + table-driven fitness `runner`
  (as in `test_harness_loop.py`): K islands advance independently; a reset kills
  the bottom half and reseeds from survivors; a win still registers.
- **Influence pool:** with a `_HubByIdentity` fake, assert the union (not
  intersection) is returned, specialists included, sampling stays in top-k, and
  hub errors fall back to the seed.

## 9. Scope & build order

**In:** the three components above, as one unit.
**Out / deferred:** the held-out verifier; hub-holds-the-population; per-island
sub-populations; cross-island crossover-by-reference; verifier-trust weighting
in selection (#12 Direction 2 — needs the verifier).

**Recommended build order** (each independently shippable & testable):
1. **Brief overhaul** (Component A) — cheapest, highest immediate value; the
   current brief is the most acute problem.
2. **Islands** (Component B) — the structural plateau-breaker.
3. **Coverage-aware influence** (Component C) — makes seeding/reset draw on
   specialists; most valuable once generalist (role/glob) runs are common.

## 10. Files

- `src/nethackers/harness/brief.py` — A1/A2/A3.
- `src/nethackers/harness/aggregate.py` — pure A2 helpers over `Evidence.results`.
- `src/nethackers/harness/loop.py` — islands (list of elites, round-robin,
  reset), per-island `recent_attempts`, seed/reset from the influence pool
  (subsumes `migrate`).
- `src/nethackers/harness/select.py` — `influence_pool` + `sample_seeds`
  (coverage-gated path untouched).
- Tests alongside each.
