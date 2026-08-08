# MAP-Elites and Objectives in NetHackers: Should Each Objective Get Its Own Archive?

- **Date:** 2026-08-08
- **Status:** Research analysis with a decisive recommendation
- **Question:** In NetHackers, "objectives" are character distributions (`random`, `all`, `role:valkyrie`, `character:val-dwa-law-fem`, ...). Should each objective have its own separate MAP-Elites archive, or should objectives relate to the archive some other way?
- **Sources:** `dunnolab/nethackers` (public repo: `harness/README.md`, `harness/PROTOCOL.md`, `harness/evolution/UPSTREAM.md`, `harness/evolution/problem/task.txt`, `src/nethackers/harness/evolution/kernel.py`, `src/nethackers/harness/evolution/driver.py`, `src/nethackers/sdk/objective.py`), `dunnolab/nethackers-internal` (`docs/evolution-kernel.md`, `docs/architecture.md`, `docs/starting-conditions.md`, `docs/protocol.md`, `src/nethackers_internal/hub.py`), and the published literature cited in [Section 9](#9-sources).

---

## 0. Executive summary

**Recommendation: none of the naive options. Split the question into three separate scopes — competition, selection, and presentation — and give each a different answer.**

1. **Competition scope (who may replace whom in a cell): per objective digest.** This is already forced by NetHackers' own evidence rules — scores are only comparable within an identical objective digest — and it is correct: a Valkyrie score and a Wizard score are measurements of different distributions and must never compete for the same cell.
2. **Selection scope (where parents come from): global, across all objectives.** Transfer is where the leverage is. Every candidate is a patch against one shared AutoAscend root, so a Wizard-specialist improvement is *literally the same codebase* as a Valkyrie improvement, one hub import and one local re-evaluation away. NetHackers' core already implements the safe mechanism: imported candidates are re-evaluated under the active objective before they can become parents. Multi-task MAP-Elites (Mouret & Maguire, 2020) is the formal precedent showing this cross-task selection beats independent per-task optimization at equal budget.
3. **Presentation scope (the public progress map): one identity-major map, with objectives as views over it, plus digest-scoped evidence pools underneath.** The hub should not host N free-floating MAP-Elites archives (one per objective — the objective space is combinatorially open, so N is unbounded), and it should not fold objectives into one competitive archive (breaks comparability). Instead, the hub's canonical archive should be a **derived elite map keyed by the finite 73-identity catalog** (rows) crossed with a small ordinal progress-tier descriptor (columns), filled from the per-identity aggregates that canonical full-catalog verification already produces. Every objective — `role:val`, `alignment:lawful`, `all`, even `random` (via the published natural-draw probabilities, as an estimate) — is a rollup or slice of those identity cells. The existing objective-digest-scoped `evolution_pool` remains the ground-truth evidence store beneath the map.

Concretely: **do not create per-objective MAP-Elites archives at the hub** (Option A) and **do not merge objectives into one competing archive** (naive Option B). Keep GigaEvo-style islands (Option C) exactly where they already live — *inside a single contributor's run*, where the islands differ by metric weighting (exploration vs robustness), not by character objective. Recognize that the distributed system as a whole is *already* an island model — each contributor run is an island, the hub is the migration medium, and mandatory local re-evaluation of imports is the migration filter — and formalize the hub side as the identity-major map (Option D). It is the leanest option: a SQL view over data the hub already stores, zero live execution, zero task assignment, and it converts the cross-role score-incomparability problem into a partition instead of trying to solve it.

Deferred: within-identity behavior descriptors at the hub (start with progress tier only), per-role islands in the local default harness, a weighted natural-draw rollup estimator, quantile/CVT binning, and Pareto cell replacement.

---

## 1. A working primer on MAP-Elites and Quality-Diversity

### 1.1 Two different numbers: fitness vs. the behavioral descriptor

Every candidate solution in a quality-diversity (QD) system is measured twice, and the two measurements play completely different roles:

- **Fitness (quality)** is the scalar you are trying to maximize. In NetHackers: the game-quality metric — score, progress, an ascension-weighted composite. Fitness answers *"how good is it?"*
- **The behavioral descriptor** (also called behavior characterization, feature descriptor, or measures) is a low-dimensional vector describing *how the solution behaves*, independent of how good it is. It answers *"what kind of solution is it?"* For a NetHack bot: how deep does it get, how many dungeon branches does it visit, does it crash, does it survive long.

The descriptor is **not optimized**. It is used to *organize* the search. This distinction is the single most important thing to hold onto: quality decides who wins a cell; behavior decides *which cell the contest happens in*.

### 1.2 The archive and the algorithm

MAP-Elites ("Multi-dimensional Archive of Phenotypic Elites", Mouret & Clune 2015, [arXiv:1504.04909](https://arxiv.org/abs/1504.04909)) discretizes the behavior space into a grid of cells and maintains an **archive**: at most one elite per cell — the highest-fitness solution ever seen whose descriptor falls in that cell. The loop is almost embarrassingly simple:

```text
loop:
  parent(s) <- select elite(s) from the archive (uniform, tournament, ...)
  child     <- mutate/crossover parent(s)
  (fitness, descriptor) <- evaluate(child)
  cell      <- discretize(descriptor)
  if cell empty or fitness(child) > fitness(elite[cell]):
      elite[cell] <- child
```

The output is not one winner but a **map**: the whole grid, showing the best-known solution of every behavioral kind. Mouret & Clune call this **illumination**: the algorithm illuminates how quality is distributed across the space of possible behaviors, rather than collapsing everything to a single point.

### 1.3 Why illumination beats pure optimization: deception and stepping stones

Pure optimizers (hill climbers, plain genetic algorithms, greedy "keep the best program" loops) fail on **deceptive** landscapes — problems where the path to the global optimum passes through regions of *lower* fitness. Pugh, Soros & Stanley (2016, [Frontiers in Robotics and AI](https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2016.00040/full)) frame QD as the cure: by forcing the population to stay spread across behavior space and running only *local* competition within each cell, the archive preserves **stepping stones** — solutions that are currently mediocre but behaviorally novel, and whose descendants later leapfrog the incumbent best.

This is not an abstract concern for NetHack. NetHack's score is deceptive in at least three concrete ways:

- **Grinding vs. descending.** A bot can raise its mean score by farming easy early floors safely; a bot that dives aggressively scores worse *on average* but is the only lineage that will ever reach Gehennom. A score-greedy loop deletes the diver. A depth-binned archive keeps it as a stepping stone.
- **Robustness vs. peak.** A patch that fixes a crash in a rare item-identification path may not move the mean score at all in a 2-episode screen, yet it is a prerequisite for every long game. A crash-rate-binned archive retains it; a score ladder discards it.
- **Specialist regressions.** A change that teaches spellcasting (needed for Wizards — the role where AutoAscend's logic is weakest, per `starting-conditions.md`) can slightly hurt melee roles and thus lower the random-draw mean. Under a single scalar it dies; in an archive that separates behavior (or, as recommended below, separates identities), it survives long enough to be combined with a melee-preserving guard.

MAP-Elites papers repeatedly find the counterintuitive result that the best single solution found by an illumination algorithm often *beats* the best solution found by a pure optimizer with the same budget — precisely because of preserved stepping stones.

### 1.4 Island models and migration

The **island model** is an older idea from parallel evolutionary computation (Whitley et al. 1999; Cantú-Paz's surveys): instead of one panmictic population, run several semi-isolated subpopulations ("islands"), each evolving independently, and periodically **migrate** a few individuals between islands. Isolation lets islands commit to different regions of the search space without a single dominant lineage taking over everything (slowing "takeover"); migration lets a breakthrough on one island seed all the others. The knobs are migration *interval* (how often), *size* (how many migrants), *topology* (ring, star, fully connected), and *selection* (migrate the best, a random elite, a Pareto front).

The island model is the workhorse of modern LLM-driven program evolution:

- **FunSearch** (Romera-Paredes et al., *Nature* 2023, [doi:10.1038/s41586-023-06924-6](https://www.nature.com/articles/s41586-023-06924-6)) ran its program database as islands; every 4 hours it *discarded the worst half of the islands* and reseeded them from survivors — migration by extinction and recolonization. Prompts sampled k=2 programs from one island; offspring returned to that island.
- **AlphaEvolve** (DeepMind, 2025) evolved whole codebases with an evolutionary database "inspired by a combination of MAP-Elites and island-based population models" — i.e., exactly the hybrid NetHackers has adapted via GigaEvo.

The relevance for NetHackers is structural and worth stating plainly: **a distributed community of contributors, each running a local evolution loop and occasionally importing elites from a shared hub, *is* an island model.** Each run is an island; the hub is the migration medium; the import policy is the migration filter. This observation does a lot of work in Section 5.

### 1.5 The vocabulary you now have

| Term | Meaning | NetHackers instance |
| --- | --- | --- |
| Fitness | Scalar being maximized | `fitness`, `macro_role_fitness`, score/progress composites |
| Behavioral descriptor | Low-dim "what kind of program" vector | depth, branch coverage, crash rate, validity |
| Cell / niche | One discretized descriptor bucket | e.g. depth-bin 3 × coverage-bin 1 × no-crash × valid |
| Archive | Best elite per cell | `MapElitesIsland._cells` |
| Illumination | Mapping quality across behavior space | the public progress map (proposed) |
| Island | Semi-isolated subpopulation with its own selection pressure | `exploration` / `robustness` islands; also: each contributor's run |
| Migration | Periodic exchange of elites between islands | `IslandPool.migrate()`; also: hub import + re-evaluation |

---

## 2. GigaEvo, and what NetHackers v1 actually adapted

### 2.1 GigaEvo at the pinned commit

GigaEvo ([AIRI-Institute/gigaevo-core](https://github.com/AIRI-Institute/gigaevo-core), pinned at `9b8687ebaf1708962370ea82b4cf2480d74874e5`; paper: [arXiv:2511.17592](https://arxiv.org/abs/2511.17592)) is an open-source AlphaEvolve-style framework: LLMs generate program mutations, an async DAG evaluates them, and a MAP-Elites layer with multi-island strategies organizes the population (validated on AlphaEvolve's Heilbronn-triangle, circle-packing, and kissing-number problems). Per `harness/evolution/UPSTREAM.md`, the pinned QD layer offers: a `BehaviorSpace` with linear/log/sqrt binning; one elite per cell per island; replacement by weighted scalar sum *or* Pareto dominance; random/fitness-proportional/tournament/Pareto parent selection; archive-size eviction policies; migrant selection policies; and multiple islands that "can use different behavior spaces and objectives, receive routed mutants, divide a generation's mutation quota, and periodically exchange programs" (default migration *moves* a program, with rollback).

NetHackers deliberately did **not** vendor GigaEvo's Redis storage, Hydra config, LangGraph mutation graph, DAG executor, model clients, or services — those duplicate ownership already held by GitHub (lineage), the evaluator (metrics), the coding agent (mutation), and the hub (search/provenance). It adapted five ideas (`docs/evolution-kernel.md`):

1. Discretize behavioral metrics into MAP-Elites cells.
2. Keep the best candidate per cell instead of one global winner.
3. Select elite parents and combine complementary behavior regions.
4. Use multiple islands with different objectives.
5. Periodically migrate elites between islands.

### 2.2 The five ideas as implemented (`src/nethackers/harness/evolution/kernel.py`)

The kernel is small and readable. The load-bearing specifics:

- **`BehaviorDimension`** — linear binning only: `coordinate = int((v - low)/(high - low) * bins)`, clamped. (GigaEvo's log/sqrt binning was not carried over — relevant later for heavy-tailed NetHack scores.)
- **`MapElitesIsland`** — one dict `cell -> Program`; the island's "objective" is a tuple of `(metric, weight)` pairs; `score(p) = Σ weight·metric`; replacement requires **strict** improvement (`<=` loses). Parent selection is tournament-3 over the island's elites by island score.
- **`IslandPool.add`** — **round-robin routing**: each newly evaluated program is offered to exactly *one* island (the route cursor is persisted in the durable snapshot). Only seeding (`seed()`) touches every island. So islands see disjoint streams of newcomers; migration is what re-couples them.
- **`select_parents`** — pick a random populated island, tournament-select a **primary** parent there, then pick a **complementary** parent: the elite (from *any* island) maximizing behavior-space distance from the primary (ties broken by fitness). This is idea 3 — deliberately crossing complementary behavior regions, e.g. "deep but crashy" × "shallow but robust".
- **`migrate`** — every 5 generations, each island *offers a copy* of its best elite to the next island in a ring; the destination admits it only if it wins its cell there. Unlike GigaEvo's default move-with-rollback, **the source keeps its copy** — migration is pollination, not relocation.
- **`default_island_pool`** — two islands:

| Island | Behavior space (dims × bins) | Weighted objective |
| --- | --- | --- |
| `exploration` | `mean_depth_norm`×10, `mean_branch_coverage`×4, `crash_rate`×2, `is_valid`×2 (160 cells) | `fitness` + 0.25·`mean_progress` − 0.3·`crash_rate` |
| `robustness` | `macro_role_fitness`×8, `worst_progress`×8, `crash_rate`×2, `is_valid`×2 (256 cells) | `macro_role_fitness` + 0.5·`worst_progress` − 0.3·`crash_rate` |

The driver (`driver.py`) adds the federation behavior: every **3** generations it queries the hub *scoped to the run's objective digest*, imports the first unseen top-fitness match, and feeds the core-admitted, locally re-evaluated result through ordinary island routing (a bounded memory of 256 seen digests survives restarts).

### 2.3 What "islands with different objectives" actually means here — and what it does not

This phrase is the source of the design confusion, so it deserves surgical precision. In GigaEvo and in the NetHackers kernel, an island's "objective" is a **scalarization** — a weighted formula over the metric vector — optionally paired with its own behavior space. The two default islands both evaluate programs under the *same* character distribution (the run's objective); they differ in *what they reward* (exploratory fitness vs. worst-case robustness) and *what diversity they preserve* (depth/coverage vs. role-balance/worst-progress). Migration then lets a program bred under one selection pressure compete under the other.

**"Islands with different objectives" in the GigaEvo sense is NOT "islands with different character distributions."** A NetHackers objective (`role:val`, `random`, ...) is an *evaluation contract*: it changes which episodes are run and therefore what the metric vector even measures. An island objective is a *reweighting* of one already-measured metric vector. Conflating the two is exactly how one arrives at the (tempting, wrong) idea that objectives should simply each become an island or an archive. The next two sections untangle this.

---

## 3. Objectives in NetHackers, as built

### 3.1 The objective contract

From `src/nethackers/sdk/objective.py` and `docs/starting-conditions.md`:

- Objective kinds: `random` (NetHack's natural draw), `identity` (`character:val-dwa-law-fem`), `set` (`characters:a,b,c`), `slice` (`role:` / `race:` / `alignment:` / `gender:`), `catalog` (`all`).
- The identity space is **finite and small**: 13 roles × 5 races × 3 alignments × 2 genders, masked to **38 legal role/race/alignment combos = 73 legal identities** (Valkyrie is female-only). The catalog publishes each identity's exact **natural-draw probability** (from 1/26 to 1/208 — NetHack picks role first, then compatible race/alignment/gender, so `random` is *not* uniform over the 73).
- Every objective canonicalizes to a **digest** over catalog version, kind, spec, and ordered identity codes. The protocol is explicit: *"Self-reported results may be ranked or selected together only when this digest is identical."* Evaluation cost profiles (`smoke`/`dev`/`challenge`/`submission`) change episode budgets, never the objective.
- Objectives **overlap and nest**: `character:val-dwa-law-fem` ⊂ `role:val` ⊂ `alignment:law` ⊂ `all`; `random` is a probability-weighted mixture over the same 73 identities (with the important caveat that random-`@` selection consumes RNG draws, so a forced-identity episode is *not* a conditional replay of a random one — the docs are emphatic that these are different protocols whose scores must never be merged).

### 3.2 What the hub archive is today

The hub (`src/nethackers_internal/hub.py`) is a single Python process over SQLite. Its "archive" is `evolution_pool(policy, objective_digest, evidence_protocol_digest, score_evidence)`: a **flat, derived, ranked index** over registered submissions. When an objective digest is given, ranking is (verified-first, fitness, reputation, digest). There are no behavior cells at the hub. Additional derived views: the canonical leaderboard (full-catalog verified only; primary key = equal-weight **macro-role fitness**, so all 13 roles count equally despite unequal identity counts), objective-scoped community leaderboards, per-role "condition cells" in the frontend, and search by role/race/alignment/gender/identity/owner/evidence.

Two facts here are architecturally decisive:

1. **The production canonical suite is full-catalog.** `canonical-forced-identity-v4` covers all 73 identities (`_canonical_objective` resolves to `all` at 73 episodes; smaller configurations become a deterministic identity subset for dev/smoke contours), and evaluation aggregates already include **per-role and per-identity score/progress/fitness**. So every production-verified candidate arrives at the hub with a measurement for *every* identity, regardless of which objective its contributor was optimizing.
2. **One genome collects evidence under many objectives** (`starting-conditions.md`: "One genome can collect evidence for many objectives in separate runs"). Candidates are content-addressed patches against one pinned AutoAscend root; the objective is a property of *evidence*, not of the *genome*.

### 3.3 The two layers of "archive"

The question "should each objective have its own archive?" is ambiguous between two layers with very different economics:

- **Layer 1 — local search state.** A contributor's `IslandPool`: private, cheap, restructurable at will, snapshot-recoverable, and explicitly *not* a platform contract ("a sensible unattended policy, not a platform restriction").
- **Layer 2 — the hub's canonical global archive.** Public, derived (no live execution, no task assignment), doubling as gene pool and progress map. This must be lean, legible, and honest about evidence.

The recommendation must answer for both layers, and the answers differ.

---

## 4. Sharpening the question: three scopes, not one

"Objective ↔ archive" bundles three independent design decisions. Naming them separately dissolves most of the difficulty:

1. **Competition scope.** When a new result arrives, *which incumbents is it allowed to displace?* This is a correctness question about score comparability.
2. **Selection scope.** When breeding, *which elites may serve as parents/influences?* This is a transfer-efficiency question. Crucially, selection does **not** require comparable scores — a parent is a source of code, not a ranking claim — and NetHackers' core already launders imports through local re-evaluation on the active objective before they can enter lineage.
3. **Presentation scope.** *What single picture does the community stare at?* This is the illumination question — the map is the product.

The literature makes the same cut. In **Multi-task MAP-Elites** (Mouret & Maguire, GECCO 2020, [arXiv:2003.04407](https://arxiv.org/abs/2003.04407)), the archive holds one cell per *task* (competition strictly within a task), while **selection is global across all tasks' cells**; a candidate bred from any tasks' elites is evaluated on one task and competes only there. On a 10-D arm with ~5,000 tasks and a hexapod with 2,000 tasks, this beat running an independent optimizer (CMA-ES) per task at the same total budget — because *similar tasks have similar solutions*, and cross-task selection exploits that without ever comparing fitness across tasks. Its successors ([Multi-Task Multi-Behavior MAP-Elites](https://arxiv.org/abs/2305.01264), [Parametric-Task MAP-Elites](https://arxiv.org/abs/2402.01275)) refine the same separation. NetHackers' character objectives are a textbook multi-task setting: 73 tasks, one shared genome representation, obviously correlated task difficulty.

With the three scopes in hand, the options can be evaluated honestly.

---

## 5. The options

### Option A — one separate MAP-Elites archive per objective

Each objective digest owns a full archive (its own behavior grid, elites, and history), locally and/or at the hub.

- **Comparability: clean.** Nothing ever competes across objectives. This is A's one genuine virtue, and it must be preserved by whatever wins.
- **Transfer: bolted on or absent.** A `role:wiz` archive learns nothing from a `role:val` breakthrough unless someone manually imports. The MT-ME result says exactly this setup (independent per-task optimization) is the baseline that loses.
- **Leanness: poor, and unboundedly so.** The objective space is *combinatorially open* — any of the 2^73−1 character sets is a legal objective with its own digest, and every evidence-protocol digest multiplies the namespaces. "One archive per objective" at the hub means an unbounded family of stateful grids, most permanently near-empty. The hub would also need to pick and version a behavior-space definition per objective — new contract surface for a thin hub.
- **Progress clarity: poor.** The public map fragments into N disconnected leaderboards. "Which roles can we ascend?" becomes a join across archives. Overlap is hidden: `character:val-dwa-law-fem` progress is invisible from the `role:val` archive even though one is a subset of the other.
- **Compute fragmentation: institutionalized.** Contributors are already spread thin; A guarantees `role:heal` stays empty until someone volunteers to farm it, and their work benefits nobody else's archive.

**Verdict: reject** as the system structure. (Its comparability discipline survives as the competition-scope rule.)

### Option B — one shared archive with objective folded in as a dimension

A single grid whose descriptor includes "objective" as a coordinate alongside depth/coverage/etc.

- **The naive version is incoherent.** Two sub-problems: (i) if objective-coordinate is just another axis but replacement compares fitness across cells' neighbors or normalizes fitness globally, you are comparing Valkyrie numbers to Wizard numbers — which the protocol rightly forbids and which is meaningless (identity changes inventory, gods, quest, monster relations, and viable strategy; AutoAscend's spell-centric play is explicitly incomplete, so Wizard scores are structurally depressed). (ii) "Objective" is not a scalar dimension: arbitrary sets don't embed on an axis, and `random` is a *distribution*, not a coordinate.
- **The refined version is not really Option B anymore.** If the objective coordinate strictly *partitions* the archive — cells never compete across the objective axis, fitness is only compared within a slice — then B is mathematically identical to "per-objective archives with a shared index and global selection," i.e., multi-task MAP-Elites. That refinement is good; it is Option D below with the base axis chosen properly (identities, not free-form objectives).
- One more trap: making objective a descriptor dimension implies every candidate gets *placed* by its objective — but a candidate evaluated under `all` has evidence for every identity at once. Objective is a property of the *measurement*, not of the *program*; descriptors describe programs.

**Verdict: reject the naive form; its salvageable core (partition axis + within-slice competition + global selection) is absorbed into D.**

### Option C — islands-per-objective with periodic migration (GigaEvo-style)

Run one island per objective (locally, or notionally across the community) and migrate elites on a schedule.

- **What C gets right:** migration-with-re-evaluation is exactly the safe transfer mechanism. An elite from the `role:val` island is *re-scored* under `role:wiz` when it lands there, so no cross-objective score comparison ever happens. Islands-as-selection-pressures (Section 2.3) are also genuinely useful and already shipped.
- **What C gets wrong as a global structure:** it re-imports Option A's fragmentation with a timer attached. More importantly, **it already exists — implicitly and better.** The distributed system *is* the island model: every contributor run is an island (with its own inner islands); the hub is the migration medium; `ProgramImport` + mandatory local re-evaluation on the active objective is the migration operator; the built-in harness's every-3-generation objective-scoped hub refresh is the migration schedule; ancestor trust policies are migration filters. FunSearch's centrally scheduled migration (reset worst islands every 4 h) worked because one operator owned all compute; NetHackers' hub deliberately owns no execution and assigns no tasks, so *scheduled* global migration is off the table — migration must remain pull-based, which is what's built.
- Locally, per-objective islands inside one run only make sense when the run's objective is wide (`all`, a big slice): e.g. one island per role, migrating candidates that get re-aggregated per-role from the same full-catalog evaluations. That is a harness-level refinement, not a platform structure.

**Verdict: keep C exactly where it lives today** — inner islands per selection pressure inside a run, and the community-as-island-model at the federation layer. Do not add per-objective island state to the hub.

### Option D (recommended) — identity-major canonical map; objectives as evidence namespaces and views; global selection

Structure the system as multi-task MAP-Elites with the **73-identity catalog as the task axis**:

- **Base coordinates: identity.** The hub's public archive is a derived elite map: **73 identity rows × a small ordinal progress-tier column** (Section 7.2), optionally × evidence class (verified / self-reported kept visually separate, as the hub already does everywhere). Cell contents: the best candidate digest for that identity at that tier, per NetHackers' existing ranking keys. Populated from the **per-identity aggregates the hub already stores** — the production canonical suite covers the full catalog, so a single verification updates all 73 rows in one pass.
- **Competition: within (identity-cell × evidence-namespace) only.** Nothing new to enforce — the objective-digest and protocol-digest rules already do this. The flat `evolution_pool` remains the ground truth beneath the map.
- **Objectives become three cheap things instead of archive owners:** (i) an *evaluation contract* for runs (unchanged — digests, suites, profiles); (ii) a *view/rollup* over identity rows for display (`role:val` = the Val rows; `alignment:law` = the lawful rows; `all` = the whole map; `random` = a natural-probability-weighted rollup **labeled as an estimate**, since forced-identity and random-`@` are different RNG protocols — measured `random`-digest evidence keeps its own pool and is never silently merged); (iii) a *search filter* for parent selection (already shipped: `/v1/evolution/search?character=...`, role/slice filters, objective digests).
- **Selection: global.** Any harness may select parents/influences from any cell of the map; the core re-evaluates imports under the active objective before they enter lineage (already enforced). The recommended default-harness tweak: when targeting objective O, pair a *specialist* parent (best in O's weakest identity rows) with a *generalist* parent (best `all`/macro-role elite) — precisely the two-parent rewrite pattern `starting-conditions.md` already sketches.
- **Local layer: unchanged defaults.** Keep the two GigaEvo islands (exploration/robustness) per run; their metrics are computed under the run's objective, so local archives are per-objective *automatically*, at zero coordination cost, and are discarded/kept at the contributor's discretion.

Why identity (not objective) as the base axis: identities are **finite (73), non-overlapping, exhaustive, and stable** — the atoms every objective is made of. Objectives are unbounded, overlapping, and user-authored. Maps need atomic coordinates; queries can be arbitrary.

### 5.5 Scoring against NetHackers' criteria

| Criterion | A: per-objective archives | B: objective as dimension (naive) | C: objective islands + migration | D: identity-major map + views |
| --- | --- | --- | --- | --- |
| Cross-objective score comparability | Clean by construction | **Broken** (cross-role comparison) | Clean (re-eval on migrate) | Clean (partition by identity × namespace) |
| Transfer / reuse of building blocks | None by default | Uncontrolled | Good, on a timer | **Best**: global selection + core re-eval (MT-ME precedent) |
| Distributed coordination cost / leanness | Unbounded archive family at hub | New comparability machinery | Requires migration scheduling the hub must not do | **Near zero**: SQL view over existing per-identity aggregates; pull-based |
| Progress-tracking clarity | N disconnected boards | One board, meaningless orderings | Board per island | **One map**; coverage % is the community QD-score; "can we ascend a Priest?" readable at a glance |
| Compute fragmentation | Institutionalized | Hidden | Reduced within a run | **Pooled**: every `all`/canonical run feeds all 73 rows; slice runs deepen their rows; every elite is one import away from any objective |

---

## 6. The recommendation, in full

**Adopt Option D.** Specifically:

1. **Hub (presentation + gene pool):** add a derived **identity-major elite map** — 73 rows × progress tiers × evidence class — materialized from existing per-identity aggregates in submissions and canonical evaluations. It is a view, not a service: no live execution, no task assignment, no new stateful archive objects, no new schema for contributors. Keep `evolution_pool` and the digest-scoped comparability rules exactly as they are; the map sits on top. Retire the idea of hub-side per-objective MAP-Elites grids permanently.
2. **Competition rule (already law, now stated as the archive's law):** a candidate may displace an elite only within the same identity cell and evidence namespace. Slice/`all`/`random` leaderboards are rollups, and `random` rollups are labeled estimates distinct from measured `random`-digest evidence.
3. **Selection rule:** parents and influences may come from anywhere on the map; admission into lineage requires core-mediated import + re-evaluation under the active objective (already enforced). Update the built-in harness's hub refresh from "first unseen top-fitness match on my digest" toward "specialist for my weakest identity row + generalist," keeping the same import machinery.
4. **Local runs:** keep the two-island GigaEvo kernel as the default. Contributors optimizing `role:wiz` get a per-objective local archive automatically because their metrics are measured under `role:wiz`. Per-role inner islands for wide objectives are a harness experiment, not a platform change.

Why this fits NetHackers' constraints better than the alternatives:

- **Symbolic, deterministic, single-root genomes** make transfer unusually cheap and safe — a candidate is a patch, evaluation is seeded and deterministic, and re-scoring an import under your objective is one local run. The system should therefore maximize selection scope, which D does and A forecloses.
- **Distributed contributors, thin hub.** D adds zero coordination: the hub stays a derived index; contributors pull. C's scheduled migration and A's archive family both push work onto the hub the design explicitly refuses ("no live execution, no task assignment").
- **Per-character objectives with few contributors.** The map pools everyone's compute: canonical verification of *any* candidate updates *every* identity row, so even a community of five people optimizing five different slices produces one coherent, filling-in picture — and the empty cells are themselves the roadmap ("nobody has taken a Healer past Sokoban").
- **Leanness above all.** The entire delta is one materialized view and one selection heuristic. Everything else is recognizing structure that already exists.
- **Precedent.** MT-ME for the formal structure and the equal-budget win over per-task optimization; FunSearch/AlphaEvolve for islands + MAP-Elites as the proven LLM-evolution memory; GigaEvo for the kernel already adapted; and the NetHack Challenge report (Hambro et al. 2022, [arXiv:2203.11889](https://arxiv.org/abs/2203.11889)) for the domain fact that motivates everything — symbolic bots dominate but *no agent came close to ascending*, so the map's upper tiers are the honest frontier.

---

## 7. Behavioral descriptors for NetHack

Descriptors must (a) be measurable under the run's evaluation budget, (b) capture axes along which stepping stones differ, and (c) stay low-dimensional (2–4 effective dims; grid size = product of bins must stay small relative to evaluation throughput — a 2-episode screen fills cells slowly).

### 7.1 Local archives (screen budget: 2 × 5,000-action episodes)

The shipped defaults are well chosen; keep them, with notes:

- **`mean_depth_norm` (10 bins)** — the single best cheap progress proxy; meaningfully spread even at 5k actions.
- **`mean_branch_coverage` (4 bins)** — distinguishes Mines-divers from Sokoban-solvers from mainline rushers; branches (Mines, Sokoban, later Quest/Ludios/Vlad's/Gehennom/Planes) demand different competencies, so this axis preserves genuinely different program kinds.
- **`crash_rate` (2 bins)** — with 2 episodes this takes values {0, 0.5, 1}; two bins = "ever crashes" vs "never", which is the right screen-level question. Keep it in the descriptor *and* negatively weighted in fitness (as shipped): the descriptor preserves crashy-but-deep stepping stones, the weight pressures them to heal.
- **`is_valid` (2 bins)** — cheap guard so invalid-but-instructive candidates don't occupy real cells.
- **`worst_progress` / `macro_role_fitness` (robustness island)** — note these are only informative when the objective spans multiple identities; under `character:...` they degenerate. A per-objective-aware default (collapse the robustness island's role dims for single-identity runs) is a small worthwhile fix.
- **Caveat to record:** using `macro_role_fitness` as *both* a behavior dimension and the dominant fitness term (robustness island) makes that axis a quantized hall-of-fame along fitness — a known, pragmatic GigaEvo pattern (their paper's fitness × validity space), fine as a diversity backstop, but it is not "behavior" in the QD sense; don't add more fitness-like dims.
- **Discretization:** linear bins are right for the normalized [0,1] metrics. If raw **score** ever becomes a descriptor, use log bins (NetHack scores are heavy-tailed; GigaEvo had log binning, the NetHackers kernel currently implements linear only). Avoid quantile binning entirely — bin edges must be *stable across contributors and time* or cell identities (and snapshots, and any future shared-cell talk) silently shift; the pinned GigaEvo's quantile enum was a stub anyway.

### 7.2 Hub map columns (verified / full-horizon budget)

At the hub, behavior should be an **ordinal progress-tier**, not raw depth — depth conflates branches (Mines End at ~Dlvl 13 is not Castle at ~Dlvl 25+) and score is too deceptive to organize the public map. A NetHack-native milestone ladder, derivable from data the trusted replay already extracts (score, depth, visited levels/branches, turns, terminal state):

```text
T0 died on Dlvl 1-3          T5 Quest completed (role-specific)
T1 reached Mines             T6 Castle / Medusa passed
T2 Sokoban solved            T7 Gehennom entered / invocation
T3 Mines End / luckstone     T8 Amulet retrieved / Planes
T4 reached Dlvl 10+ mainline T9 ASCENSION
```

(Exact cutpoints to be fixed against what the replay can attest; the shape — ~8–10 ordered tiers ending at ascension — is the point.) The public map is then **73 identities × ~10 tiers**, with the best verified candidate per cell and self-reported cells rendered separately. Coverage percentage of this grid is the community's QD-score; the rightmost column is the win condition. Within-identity secondary descriptors (branch coverage, turn efficiency, conduct flags) are deliberately deferred — add columns only when tiers saturate.

### 7.3 NetHack-specific cautions

- **Budget censoring.** Screen metrics (5k actions) and verified metrics (up to 10^8 actions) live in different regimes; a descriptor like "ascended" is unmeasurable at screen budget. The evidence-protocol digest already keeps these namespaces apart — local archives bin screen metrics, the hub map bins verified metrics, and the two must never share cells.
- **Determinism vs. variance.** Episodes are seed-deterministic, but 2-seed aggregates are high-variance estimates of the objective distribution; strict-improvement replacement on noisy 2-episode metrics will churn cells. Mitigations (deferred): more episodes for elite-confirmation, or MAP-Elites noisy-domain re-evaluation tricks. Never "fix" this by letting the harness see or memorize seeds — the task contract forbids it.
- **Identity resolution.** Per-identity binning requires resolved identity; forced suites resolve at reset, random-`@` runs resolve via welcome-message/xlog parsing and may remain `@` if unresolved. Identity-cell updates should only consume identity-resolved episodes (the aggregates already respect this).
- **Role structure is not symmetric.** Roles differ in legal identity counts (Wizard 10, Knight 2, Valkyrie 3) and in bot-tractability (Valkyrie's melee start suits AutoAscend; spell-centric roles hit its acknowledged gaps). Equal-weight macro-role fitness already handles the ranking side; the identity-major map handles the archive side by never comparing across roles at all.

---

## 8. Deferred (deliberately)

1. **Within-identity behavior columns at the hub** (branch coverage, turn efficiency, conducts) — until tier coverage saturates.
2. **Per-role inner islands** for wide-objective runs — a default-harness experiment behind the existing snapshot schema.
3. **Natural-draw rollup estimator** for `random` from identity cells with published probabilities — ship only with an "estimate" label and the RNG-protocol caveat; measured `random`-digest evidence remains authoritative.
4. **Quantile/CVT binning and Pareto replacement** — GigaEvo has them; NetHackers' linear+weighted-scalar simplification is right for v1. Revisit if descriptor dims grow past ~4 (CVT-MAP-Elites, [arXiv:1610.05729](https://arxiv.org/abs/1610.05729)) or metric trade-offs demand Pareto cells.
5. **Any hub-driven task assignment / bandit over objectives** ("the map says Healer-T2 is empty, go work on it" as a *suggestion* is fine as UI; as *assignment* it violates the thin-hub principle). Contributors choose their objectives; the map makes the gaps legible and lets social incentives do the routing.
6. **Elite-confirmation episode budgets** for noisy cell replacement (Section 7.3).

---

## 9. Sources

**Project (read via GitHub CLI, 2026-08-08):**
- `dunnolab/nethackers-internal`: `docs/evolution-kernel.md`, `docs/architecture.md`, `docs/starting-conditions.md`, `docs/protocol.md`, `docs/onboarding.md`, `src/nethackers_internal/hub.py` (`evolution_pool`, `_canonical_objective`, community leaderboard).
- `dunnolab/nethackers`: `README.md`, `harness/README.md`, `harness/PROTOCOL.md`, `harness/evolution/UPSTREAM.md`, `harness/evolution/problem/task.txt`, `src/nethackers/harness/evolution/kernel.py`, `src/nethackers/harness/evolution/driver.py`, `src/nethackers/sdk/objective.py`.

**Literature:**
- Mouret & Clune (2015). *Illuminating search spaces by mapping elites.* [arXiv:1504.04909](https://arxiv.org/abs/1504.04909).
- Pugh, Soros & Stanley (2016). *Quality Diversity: A New Frontier for Evolutionary Computation.* [Frontiers in Robotics and AI](https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2016.00040/full).
- Mouret & Maguire (2020). *Quality Diversity for Multi-task Optimization.* GECCO. [arXiv:2003.04407](https://arxiv.org/abs/2003.04407).
- Anne & Mouret (2023). *Multi-Task Multi-Behavior MAP-Elites.* [arXiv:2305.01264](https://arxiv.org/abs/2305.01264); (2024) *Parametric-Task MAP-Elites.* [arXiv:2402.01275](https://arxiv.org/abs/2402.01275).
- Vassiliades, Chatzilygeroudis & Mouret (2018). *CVT-MAP-Elites.* [arXiv:1610.05729](https://arxiv.org/abs/1610.05729).
- Romera-Paredes et al. (2023). *Mathematical discoveries from program search with large language models* (FunSearch). [Nature](https://www.nature.com/articles/s41586-023-06924-6) — islands; worst-half reset every 4 h.
- DeepMind (2025). *AlphaEvolve* — program database inspired by MAP-Elites + island models ([overview](https://www.emergentmind.com/topics/alphaevolve-paradigm)).
- AIRI Institute. *GigaEvo.* [GitHub @9b8687e](https://github.com/AIRI-Institute/gigaevo-core/tree/9b8687ebaf1708962370ea82b4cf2480d74874e5); paper [arXiv:2511.17592](https://arxiv.org/abs/2511.17592).
- Hambro et al. (2022). *Insights From the NeurIPS 2021 NetHack Challenge.* [arXiv:2203.11889](https://arxiv.org/abs/2203.11889) — AutoAscend (symbolic) won; no entrant approached ascension.
- Whitley, Rana & Heckendorn (1999). *The Island Model Genetic Algorithm: On Separability, Population Size and Convergence*; Cantú-Paz (1998). *A Survey of Parallel Genetic Algorithms* — classical island-model/migration theory.
