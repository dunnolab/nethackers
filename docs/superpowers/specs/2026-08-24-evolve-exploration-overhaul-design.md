# Evolve Exploration Overhaul — Design

**Goal:** make the evolve loop actually keep improving past its early plateau, by
fixing three interlocking weaknesses in one unit: the mutator is briefed nearly
blind, the search is a single lineage with no diversity, and the parent selector
discards specialists.

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
    metric"* and includes an **outcome tally**; the set branch **drops both**.
  - It propagates **one aggregate number per build** and nothing else. The
    eval's rich per-episode signal (how the game ended, dungeon depth, milestone
    reached, death cause) is thrown away.
  - There is **zero memory across attempts**: every mutation is a fresh agent
    that never sees what prior tries changed or how they scored.

There is also a known selection gap (issue #12): the parent selector is
**coverage-gated** — a program can only parent an objective `S` if it has
results for *all* of `S`. Correct for the leaderboard, wrong for *influence*: a
run over a role whose specialists each cover one build gets an empty pool and
cold-starts from the seed, discarding every specialist.

**The key reframe:** the mutator is a *coding agent* — its core skill is reading
and analyzing code. The fix for "briefed blind" is therefore **not a richer text
brief** (distilling code for a model that reads code is lossy and is machinery we
would have to build). It is to **provision the sandbox with the actual solution
folders + a compact manifest and let the agent analyze them** — keeping in text
only the task framing it cannot infer from code. That reframes Component A
(below); islands (B) and coverage-aware selection (C) then decide *which* folders
get provisioned.

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

## 3. Component A — Sandbox provisioning + task framing (build first)

Highest leverage and, done this way, *less* code than the current brief. Rather
than distill the parent, its results, and prior attempts into text, **provision
the sandbox with the real folders + a small manifest and let the agent analyze
them.** Only the framing it can't infer from code stays text.

**A1. Task framing (short text preamble, both branches).** The one distillation
the monk run proved missing — the agent needs the *what/why*, not a summary of
code it can read. One short block: this is NetHack (played via NLE); the metric
is a BALROG-style progression score that starts near 0 and rises as the bot gets
deeper/further (exact milestone definition sourced from the evaluator, not
asserted); the goal is to descend, survive, progress. For a *set* objective, name
the builds and which are weakest. No strategy spoilers.

**A2. Provision the folders (replaces text-distilled feedback).** Per mutation,
lay out a read-only reference tree beside the editable base:

```
/workspace/               base to edit = the island's champion (seeded from §5, then evolved)
/refs/influences/<id>/    ≤2 more selector-chosen influences (crossover material)
/refs/attempts/<id>/      ≤3 rejected attempts since the current best
/refs/parent-eval.json    the base's raw per-episode eval output
/refs/CONTEXT.md          one table: each folder → score / outcome / hypothesis
```

- **Base (`/workspace`)** — the island's champion. It **self-documents its
  accepted lineage** via the `# hypothesis:` comment every accepted edit leaves,
  so the path-to-best needs no separate folders.
- **Influences (`/refs/influences/`)** — the *parents*, chosen by the selector
  (§5), **not** the iteration log: other strong solutions (hub specialists /
  elites) to analyze and borrow/combine. Crossover-by-reference falls out for
  free. Degrades gracefully — a thin hub pool just means fewer influence folders.
- **Recent rejected attempts (`/refs/attempts/`)** — only the last ≤3 *since the
  current best* (the ones not already folded into the base). The agent reads the
  actual rejected code + its score and judges *idea vs implementation* itself —
  which is why these are folders, not a "don't repeat" list: a good direction
  that failed on a bad implementation is visible and re-attemptable.
- **Raw eval** — the base's per-episode results (end_status/depth/milestone) as a
  file; the agent can also run its own evals (`arena.run` is already in the
  sandbox) on the base or any reference.
- **`CONTEXT.md`** — the only thing we *write*: a small manifest mapping each
  folder → score / outcome / (for attempts) hypothesis, so the agent knows which
  is which. A specialist's known-strong identity goes here too ("strong at
  `mon-hum-cha-mal`").

The text brief shrinks to: **A1 framing + "the current bot is `/workspace`;
strong references and recent attempts are under `/refs/` (see `CONTEXT.md`);
analyze them and make one focused, `# hypothesis:`-commented change."**

**Why this over a richer text brief:** less lossy (real code + real eval output,
not our summary); it leverages the agent's actual strength; and it is *less*
machinery for us — copy bounded folders + write one manifest, no tally /
where-it-stalls / diff-formatter.

**Mechanics.** `loop.py` assembles `/refs/` per iteration (copy the influence +
attempt folders, write `CONTEXT.md`); `container_operator.py` mounts `/refs/`
**read-only** into the mutator sandbox (all folders are solution code — no
info-diet-wall concern); `brief.py` shrinks to the framing + the `/refs/`
pointer. Reference sets are bounded (§6).

## 4. Component B — Islands (local diversity)

`src/nethackers/harness/loop.py`. Replace the single `elite` with **K islands**.

**State.** `islands: list[EliteState]` of length K (each: its own champion tree +
dev/validation fitness + dev evidence), a round-robin cursor, and per island the
handful of **rejected attempts since its current best** (their folders + scores +
hypotheses) that feed `/refs/attempts/` (§3).

**Seeding.** Initialize each island from the influence pool (§5) if the hub has
trusted entries for `S`, else from the cold-start seed. Distinct seeds where the
pool allows; the seed otherwise.

**Iteration (round-robin).** Iteration *i* works island `i mod K`: **provision
its sandbox (§3)** — base = its champion, `/refs/` = selector-chosen influences
(§5) + that island's recent rejects — mutate, run the existing gates (smoke → dev
→ validation). If the child beats *that island's* champion it becomes the new
champion (and its recent-reject list clears); otherwise the attempt's folder +
result join that island's recent-reject list.

**Reset (the plateau-breaker) — reseed from *diverse local survivors*, never
"the single best".** Every **T** iterations: rank islands by champion fitness,
**kill only the bottom half**; leave the **survivors untouched** (they keep
their distinct champions — that is where diversity is preserved). Reseed each
killed island from a **top-k-sampled *surviving island*** (temperature > 0, so
not always #1), clear its recent-reject list, and let it re-diverge by
independent mutation. This defunds stuck lineages and spreads winners *without*
collapsing the population onto one lineage — the pull toward winners
(exploitation) is balanced by reseed-from-diverse-survivors + re-divergence
(exploration).

The **hub influence pool is a *secondary* seed source, not the reset default**:
it seeds the *initial* islands (see Seeding) and may *occasionally* inject one
cross-run specialist at reset (e.g. one killed slot), so a run can adopt another
run's/user's win without homogenizing. This **subsumes today's mid-run
`migrate`** — as an occasional diversity injection, not an every-cycle "adopt the
single global best". (For a single-identity run the hub pool is thin early on, so
local survivor diversity carries exploration — precisely why islands exist on top
of the hub.)

**Registration.** Unchanged: any island win that beats the objective's
registered elite publishes + registers (per-identity slices for a set). The
board stays "best across everything."

**Isolation is of *state*, not information.** A win in island A never overwrites
island B's champion — that state isolation is the diversity. The reference
folders in `/refs/` are read-only *analysis* material chosen by selection (§5),
not shared island state; provisioning them does not couple the lineages.

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
fetch)` and a `sample_seeds(..., n)` that resolves *n* solutions (via the
existing `_resolve`/cache/pull) — used both to **seed islands** (§4) and to
choose the **influence references** provisioned into `/refs/` each iteration
(§3). Falls back to the cold-start seed on hub error / empty pool / unresolvable
bytes.

The leaderboard read (coverage-gated `elites(S)`) is untouched. No hub
schema/endpoint change — the union is computed client-side from the same
per-identity `hub.elites(ident)` queries the coverage-gated path already makes.

## 6. Parameters (defaults + rationale; confirm in review)

- **K (islands) = 4.** FunSearch uses ~10, but its iterations are cheap; ours are
  minutes (real mutation + Docker eval), and round-robin means K× wall-clock per
  pass. 4 gives real diversity at tolerable cost. Configurable.
- **T (reset period) = 4·K iterations** (≈ 4 mutations per island between
  resets). Enough to diverge before culling; frequent enough to defund the stuck.
  Configurable.
- **Reference counts:** **≤2 influences + ≤3 recent attempts** per sandbox — the
  read-only tree stays small; the base's lineage rides inside `/workspace`.
- **top-k / temperature:** reuse `select.py`'s existing defaults.
- **1 elite per island** (not a per-island sub-population). Minimal; per-island
  pools are a fast-follow.

## 7. Error handling

- **Hub down / empty influence pool / unresolvable bytes:** seed from the
  cold-start seed and provision no influence folders (the pool functions never
  raise — same contract as today's `top_trusted_elite`). Provisioning degrades
  to base + attempts only.
- **Operator error on an island iteration:** the existing circuit-breaker
  applies; that island simply gets no new champion this turn (no cross-island
  effect).
- **Missing per-episode fields (older evidence):** `parent-eval.json` /
  `CONTEXT.md` render only what is present (score always available; depth /
  milestone / death-cause best-effort).

## 8. Testing approach

All units stay pure/injectable (hub, rng, fetch, eval `runner` already injected).
Provisioning is filesystem-only and asserted directly; no LLM involved.

- **Provisioning + framing:** given a base, chosen influences, and recent
  rejects, assert `/refs/` is assembled with the right folders (base, ≤2
  influences, ≤3 rejects), a `CONTEXT.md` manifest mapping folder→score, and that
  the text preamble contains the NetHack frame in **both** branches.
- **Islands:** with a fake improving operator + table-driven fitness `runner`
  (as in `test_harness_loop.py`): K islands advance independently; a reset kills
  the bottom half and reseeds from survivors; a win still registers.
- **Influence pool:** with a `_HubByIdentity` fake, assert the union (not
  intersection) is returned, specialists included, sampling stays in top-k, and
  hub errors fall back to the seed.

## 9. Scope & build order

**In:** the three components above, as one unit. Crossover-by-reference is
**in** — via selector-chosen influence folders in `/refs/` (§3).
**Out / deferred:** the held-out verifier; hub-holds-the-population; per-island
sub-populations; provisioning a *sibling island's* (unregistered) champion as a
reference; verifier-trust weighting in selection (#12 Direction 2 — needs the
verifier).

**Recommended build order** (each independently shippable & testable):
1. **Provisioning + framing** (Component A) — cheapest, highest immediate value;
   the current blind brief is the most acute problem, and this works even before
   islands (base = today's single elite, refs = recent rejects).
2. **Islands** (Component B) — the structural plateau-breaker.
3. **Coverage-aware influence** (Component C) — fills `/refs/influences/` and
   island seeds with specialists; most valuable once generalist runs are common.

## 10. Files

- `src/nethackers/harness/brief.py` — shrink to A1 framing + the `/refs/` pointer
  (both branches).
- `src/nethackers/harness/loop.py` — islands (list of elites, round-robin,
  reset), per-island recent-reject tracking, seed/reset from the influence pool
  (subsumes `migrate`), and per-iteration `/refs/` assembly + `CONTEXT.md`.
- `src/nethackers/harness/container_operator.py` — mount `/refs/` read-only into
  the mutator sandbox.
- `src/nethackers/harness/select.py` — `influence_pool` + `sample_seeds`
  (coverage-gated path untouched).
- `src/nethackers/harness/aggregate.py` — small pure helpers for the `CONTEXT.md`
  score/outcome rollup over `Evidence.results`.
- Tests alongside each.
