# "Weights & Biases for harnesses" — what the wandb-analog actually is — research (2025–2026)

*Deep-research synthesis (5 parallel agents). Companion to [agentic-evolutionary-search-grounding](2026-08-10-agentic-evolutionary-search-grounding.md) and [guiding-autonomous-search](2026-08-10-guiding-autonomous-search.md). Framing question: in DL, wandb tracks the **learning** process (loss/grad/LR curves, samples, sweeps, run comparison). For a harness the process is a **solving** (or **self-improvement**) process, not gradient learning. What is the analog — and how is it different from the traces everyone already uses?*

## VERDICT
**The W&B of harness research is the *experiment layer above traces*, not richer traces.** It is infrastructure that turns every harness execution into a **comparable run** — config-identified, with **budget-indexed** progress curves, **seed-aggregated** statistics, and **versioned artifacts linked by lineage** — applied at two nested levels: (i) *inner loop* = live telemetry/steering of one solving/search process; (ii) *outer loop* = tracking the sequence of harness/bot versions that process improves. Traces stay as the per-execution **debug evidence you drill into, never the unit you compare**. Slogan: *you don't compare models by reading forward passes; you shouldn't compare harnesses by reading traces.* This layer is **missing as a product** — every AlphaEvolve-family system re-invents an ad-hoc version of it — and its shape is almost exactly what the NetHackers hub is already converging on.

Your four scattered intuitions were each correct, and they resolve cleanly:

1. **"Compare harnesses" (✓, but the field has leaderboards, not trackers).** Scaffold choice moves scores *as much as the base model* — same model, 33%→62% on SWE-bench Verified under different scaffolds; 28pp within one model on GAIA; GPT-4 5%→38% under METR's elicitation. The state of the art is **HAL** (Princeton, 2510.11977): model × scaffold × benchmark, cost on every row, Pareto accuracy-vs-cost frontiers — and it literally runs on **W&B Weave**. But HAL is a *published-claim* leaderboard (one row per (harness, model)), not a *development instrument*: no harness-version lineage, no seed-run grouping, no regression between commits, no config sweeps. That vacant middle is the product gap.
2. **"More than one eval number — track the solving process" (✓).** The loss-curve analog is precise: **best-so-far score vs compute/$** — a monotone staircase over a population quantile band. Every serious 2024–26 system argues via this exact figure (RE-Bench, ARC Prize, AlphaEvolve, ShinkaEvolve, Large Language Monkeys). Around it sit four more panels (progress %, rollout-population health, QD-archive, integrity) detailed below.
3. **"Maybe it's really about self-improving harnesses / improvement steps" (✓ — this is the reading that feels *most* wandb-like, for a real reason).** A single solve lacks training-run structure; **self-improvement restores it** — a persistent improving object (the bot/archive), an iterated improvement operator (LLM mutation), and a monotone progress functional (fitness/QD-score). *"The weights are code."* DGM's SWE-bench **20.0%→50.0%** trajectory *is* the loss curve of a self-improving harness. And the killer feature is not logging score-vs-iteration but **computing the right selection metric** (see HGM, below).
4. **"Different from traces, but how?" (✓ — fundamentally different).** A trace is n=1 forensics on an event-time axis; it has no config identity, no shared budget axis, no progress functional, no aggregation over repetitions — the four things a *comparison* requires. Traces are necessary raw material and terrible science. The agent ecosystem inherited its tooling from **production observability** (OpenTelemetry GenAI, OpenInference, LangSmith/Langfuse), so the debug layer shipped first; the **science layer** (wandb/MLflow-style) was never ported to agentic search.

---

## 1. The three layers (and why "everyone uses traces" isn't the answer)

The whole confusion dissolves once you separate three layers that the market currently blurs:

| Layer | Unit | Question it answers | Axis | wandb counterpart | Agent-stack counterpart (2024–26) |
|---|---|---|---|---|---|
| **Trace** | one execution | "what happened / why did it fail *here*?" | event time (span tree) | ~absent in classic wandb — W&B built a **separate product** (Weave) for it | OTel GenAI semconv; OpenInference; LangSmith / Langfuse / Phoenix / Braintrust |
| **Metric / curve** | one *process* | "what *shape* is this solve/search taking?" | **budget** (tokens / $ / calls / evals / generations) | `wandb.log(…, step=)` time series | largely missing; ad-hoc per-project dashboards |
| **Experiment / run** | one *configured* execution | "is B better than A, at what cost, with what confidence?" | config space | wandb Run / Sweep / Artifact / Report | largely missing (HAL is closest, eval-time only) |

Three arguments for why the trace layer — the one "everyone uses" — is structurally *not* the wandb-analog:

- **Historical.** LLM tooling descends from ops/observability culture (OpenTelemetry), optimized for debugging live systems. Experiment tracking descends from research culture (wandb/MLflow), optimized for comparison and cumulative knowledge. The first shipped for agents; the second didn't.
- **The sufficient-statistic argument.** DL has a canonical scalar for its process — the loss — so nobody compares models by reading forward passes. Harness search has **no canonical scalar**, so practitioners fall back on *reading raw traces* — substituting debugging for measurement. The research act behind "wandb for harnesses" is **defining the small set of process functionals** (envelope score, QD-score, coverage, acceptance rate, burn rate, failure taxonomy) that make trace-reading unnecessary for comparison, demoting traces to linked evidence.
- **The formal argument.** A comparison is a statement about *distributions over executions under controlled configuration*. It requires (i) config identity, (ii) a shared axis, (iii) a progress functional, (iv) aggregation over repetitions. A trace has none of the four.

Corroborating tell: **W&B ships these as two separate products** — Models (runs/sweeps/artifacts, process-centric) and Weave (traces/evals, request-centric) — and even in the agent tooling market, *"experiment"* is a false cognate. In LangSmith / Langfuse / Braintrust / Phoenix / Weave, an "experiment" = one **batch eval** (app-version × dataset × scorers → aggregate score). That is a **point** measurement / regression test, not a tracked process — no step axis, no learning-curve analog, no run in the wandb sense. **Nobody in the market positions "experiment tracking for agents"; they all say "observability / tracing / evals for agents."**

---

## 2. The translation table — what "losses, grads, samples" become

This is the direct answer to *"what is the analogy."* The mapping is not one-to-one, and the places it *breaks* are the interesting ones.

| DL learning-process object | Harness search/solving-process analog |
|---|---|
| loss curve | **best-so-far fitness & QD-score vs budget** (the monotone *envelope*) |
| gradient norm | **envelope slope**: improvement per 1M tokens / per $ |
| gradient noise scale | **fitness-eval noise**: seeds per candidate; per-point **CI width** |
| learning rate / step size | **mutation aggressiveness**: diff size, operator temperature |
| minibatch | **generation / candidate batch** |
| train–val gap | **selection-seed vs held-out-seed fitness gap** (the search overfitting alarm) |
| NaN loss / divergence | **candidate crash & non-compile rate** |
| entropy collapse | **archive coverage stall / diversity collapse** |
| per-class loss | **failure taxonomy** — for NetHack, the **death-cause distribution over generations** |
| samples panel | **elite diffs + their mutation-episode traces** |

**Why solving ≠ learning (the disanalogies that matter):**

- **No θ, no gradient.** The optimized object is external and symbolic (task success / the bot's code), not internal weights. Feedback is zeroth-order and sparse (one fitness number per candidate), and updates are LLM-proposed *mutations*, not descent steps.
- **Progress and diagnostics decouple into two curves.** In DL the loss is both the objective and the health signal. In search the **envelope** (best-so-far, QD-score) is monotone by construction, while the **proposal stream** (individual candidate scores) is a non-monotone *diagnostic of operator health*. A tracker must show both; ranking is done on the envelope, debugging on the stream.
- **Evaluation is expensive and stochastic.** DL's loss is free and dense. Harness fitness costs real compute (N NetHack seeds × minutes/candidate) and is a *noisy estimate* — so curves are sparse and **every point carries a CI**.
- **Heavy tails + non-stationarity.** NetHack outcomes are heavy-tailed (2021 NetHack Challenge: the best *symbolic* bot's **median** was ~3× the best neural bot's, ~5× on top episode) and the agent mutates its own environment (the codebase). Intermediate values weakly predict finals → DL's extrapolation/early-stopping intuitions fail.
- **Credit assignment needs lineage, not local slope.** A loss curve attributes progress to the last update. A search curve attributes nothing — you attribute a fitness jump to a *specific mutation* only via the genealogy. **Lineage is a core primitive, not metadata.**

---

## 3. RL is the closest precedent — and supplies the discipline

RL already abandoned loss as the tracked object: its canonical plot is **return-per-episode vs environment-steps** — outcome vs budget, exactly the harness shape — and it already survived the credibility crisis harness research is now entering (Henderson 2018: same algorithm, different seeds → opposite conclusions). Transplant its machinery wholesale:

1. **Budget is the x-axis.** Env-steps ≈ tokens / $ / candidate-evals. Never report a bare score; report score@budget and compare harnesses **budget-matched** (HAL's cost-controlled eval; Kapoor et al.'s accuracy-vs-cost Pareto, where simple baselines Pareto-dominated complex agents at ~50× lower cost).
2. **Sample-efficiency is a headline result**, not a footnote: track $-to-score-τ and evals-to-τ (ShinkaEvolve's whole pitch: SOTA circle packing in **~150 evaluations**).
3. **Seeds, aggregated properly.** Heavy tails → use **IQM + stratified bootstrap CIs + performance profiles** P(score > τ) (Agarwal 2021; `rliable`), not means. Repeat at **both** levels — game seeds per candidate *and* whole search-run repetitions (a single evolutionary run is an anecdote).
4. **Train/eval separation, reborn.** Fitness used by selection is adaptively re-queried noise; evolution *will* overfit the selection seeds. Report on versioned **held-out seed sets**; the eval protocol is itself an artifact.
5. **Diagnostics ≠ objective.** RL logs entropy/value-loss/KL to *explain* runs, never to *rank* them. Harness analogs: tool-error rate, acceptance rate, diff size, context pressure, crash rate.
6. **Population + lineage precedent already exists in DL:** Population Based Training (Jaderberg 2017) tracked population fitness distributions *and* hyperparameter-inheritance lineages — the closest DL-native ancestor of an evolutionary-harness dashboard.

---

## 4. The inner loop and the outer loop are one framework

**Law of re-leveling: one level's *run* is the next level's *step*.** NetHackers is a 4-level nesting, and your two "products" are two windows onto it:

- **L0 — game episode:** bot plays one seed. Trace = ttyrec/action log; scalars = score, depth, turns, death cause.
- **L1 — mutation episode:** the LLM agent edits the bot once. Trace = OTel GenAI span tree; scalars = tokens, $, tool errors, diff size, tests passed; artifact = candidate program.
- **L2 — search run:** the MAP-Elites loop. *This is the natural "wandb Run."* Step = candidate eval/generation; curves = fitness envelope, QD-score, coverage, acceptance rate, burn rate; artifacts = archive snapshots + elites with parent pointers.
- **L3 — research program:** comparing search runs across harness versions and configs; sweeps, leaderboards, reports.

- **Inner-loop product** (intuitions #2, #4): live telemetry of one L2 run, aggregating L1/L0 events into budget-indexed curves. Job = **health & steering** ("is this stalled? worth its $/hour? which operator is paying off?"), drill-down terminating in an L1 trace.
- **Outer-loop product** (intuitions #1, #3): experiment tracking across L2 runs and harness/bot versions. Job = **science** ("is v2 > v1 at matched budget, with CIs? which config wins the sweep?"). The self-improvement reading is this same machinery pointed at **artifact lineage**: the archive is the checkpoint directory, fitness-vs-budget is the loss curve, the lineage tree is the optimizer trajectory.

Both instantiate the same schema — *(identity, config, budget axis, progress functional, repetition statistics, artifact lineage, attached trace evidence)* — at adjacent nesting levels, and outer-loop data is just the aggregation of inner-loop data.

**NetHackers-specific mapping:**

| Primitive | INNER — one search run, live | OUTER — across runs & harness versions |
|---|---|---|
| **Run** | one evolution run: run-id + (harness commit, model, operator config, archive dims, budget, seed policy) | one search run as a single data point, grouped by harness version |
| **Step** | one candidate eval/generation; sub-steps = mutation episode (L1), game episode (L0) | one search run; ordered by harness version or sweep point |
| **Metric** | best-so-far & IQM fitness (selection seeds); holdout fitness; QD-score; coverage %; acceptance rate; crash rate; token & $ burn; evals/hour; tool-error rate; diff-size dist; death-cause histogram | score@budget w/ bootstrap CIs on **held-out** seeds; cost-to-threshold; QD-score & coverage at matched budget; accuracy-vs-$ Pareto position; regression Δ vs previous version; run-to-run variance |
| **Artifact** | candidate programs (git shas) + parent pointers; archive snapshots; per-candidate eval logs; mutation diffs | elite bot per run; final archive; **the harness version itself** (code + prompts + operator config); **versioned eval seed sets**; reports |
| **Sweep** | within-run operator stats: which mutation prompt/operator is paying off (bandit view) | config sweep: base model × mutation prompts × archive dims × selection temperature × evals-per-candidate |
| **Report** | live dashboard: health, stall detection, burn-vs-progress; **archive heatmap** over behavior descriptors (e.g. dungeon-depth × turns-survived) | "v_k vs v_{k−1}" with CIs; leaderboard of harness configs at matched budget; winning bot's **lineage tree annotated with which mutations bought which points** |
| **Trace** | drill-down target: one mutation episode's OTel span tree; one game's ttyrec | linked evidence behind report claims only |

---

## 5. The inner-loop dashboard — five panels

The research agents (which read `arena/trajectory.py`, `hub/atoms.py`, `hub/views/elites.py`) note your `Atom` / `TrajectoryResult` schema already carries the *endpoints* of most of these; the main gap is **within-episode time series** (dlvl/xp/progression per turn), which NLE exposes cheaply.

1. **Progress panel (one candidate solve).** Progression % vs game turns (BALROG-weighted, 2411.13543), overlaid per seed; dlvl/xp/score/HP strip charts (NLE counters); milestone timeline + achievement matrix; **ttyrec replay anchored to every point** (click a dip, watch the death).
2. **Search-efficiency panel (one run).** Best-so-far fitness vs *three* x-axes (evals / wall-clock / $) as one staircase with a p25–p75 band; coverage@k / pass@k on log-x, pass^k & G-Pass@k for reliability; **cost-of-pass** gauge and budget burn-down; tokens-per-point-of-progression.
3. **Population / diversity panel (per generation).** Per-seed outcome matrix (candidate × seed heatmap — separates *better bot* from *lucky seed*); semantic entropy / majority-vote convergence across rollouts; trajectory-embedding scatter colored by fitness; stuck-in-loop rate, invalid-action rate, overthinking-score distribution; tool-error breakdown via the **TRAIL** taxonomy (2505.08638).
4. **QD-archive panel.** Animated archive **heatmap over generations** (the harness world's native 2-D viz — the analog of watching a loss landscape), plus the standardized **QD triple**: QD-score, coverage %, max-fitness per iteration. This is *already* standard in **pyribs** (`ArchiveStats`, `ribs.visualize.grid_archive_heatmap`) and **QDax** (`plot_map_elites_results`, `CSVLogger`) — treat "archive snapshot per generation" as a first-class logged artifact type, not a user-rendered PNG. Derived metrics worth adding: **archive churn** (discovery vs displacement), **improving-mutation rate** (fraction of edits that beat their parent), **lineage depth** of the best, **cell age / staleness map**, **per-cell eval variance**.
5. **Integrity / hack panel (alarms, not analyses).** Status stacked-area (completed vs invalid_action vs bot_error vs infra_error — infra spikes mean *pause the run*, not "fitness dropped"); **held-out-seed vs selection-seed gap** curve (the overfitting alarm); CoT-monitor hack flags (2503.11926 — *log, don't select on them*, or you train obfuscated hacking); solution-complexity drift; a suspicious-jump detector that auto-re-evals >Nσ leaps on fresh seeds; a Docent-style open query box over the transcript corpus.

> The recurring cross-system lesson: **transcripts are the gradient norms.** Scalar curves detect *that* something changed; only artifact-linked drill-down (curve point → ttyrec / code diff / generating prompt) explains *why*. OpenEvolve, ShinkaEvolve, AIDE, and Docent independently converged on tree-plus-transcript UIs. **The x-axis is a hierarchy, not a step counter** (env-step < episode < candidate < generation < run) — first-class roll-up across levels is the core schema decision, and your Evidence→Atom design already anticipates it.

---

## 6. The outer-loop tracker — an evolutionary-run tracker

**Everyone already builds a bespoke version of this**, and it converges on the *same shape*: a **lineage DAG of harness/bot versions + an archive + per-node metric vectors**, in a purpose-built store — DGM (`output_dgm/` JSON), HGM (custom `tree.py`), **Vesper (SQLite over git worktrees)**, **ShinkaEvolve (SQLite + web visualizer)**, OpenEvolve (program DB + checkpoints + Flask tree UI), **GigaEvo (Redis records with `lineage{parents,children,mutation,generation}`)**, **EvoGit (git *is* the tracker)**. wandb appears **once** in the whole corpus — as ShinkaEvolve's *optional mirror* of derived scalar curves. The primary key is a **DAG of code artifacts with metric vectors**, which wandb's metric-vs-step model cannot index; the per-generation curves are *views over the genealogy*, not the ground truth.

**The sharpest single result — the tracker's killer feature is computing the *right* metric, not logging one:** HGM's **Metaproductivity–Performance Mismatch** (2510.21614). A node's own benchmark score correlates only **0.27–0.44** with its lineage's eventual improvement, while **Clade Metaproductivity** (aggregate success of the whole *subtree*) correlates **0.51–0.87**. So *"which metric do you track/select on"* stops being a dashboard preference and **becomes the search algorithm itself** — selecting on the displayed per-node score is quantifiably wrong. A wandb-for-harnesses that ships **CMP / subtree rollups, novelty scores, and bandit state as first-class derived metrics** encodes the field's main methodological lesson; one that ships only score-vs-iteration reproduces the mismatch *in UI form*.

The selection signal is a spectrum the tracker must support: raw node score (SICA — weakest, crashed) → sigmoid(score)×novelty-by-children (DGM) → **clade CMP + Thompson sampling (HGM — best)** ≈ performance×√novelty (GEA) → embedding-novelty + UCB1 bandit over LLMs (ShinkaEvolve) → MAP-Elites cell competition (OpenEvolve/GigaEvo) → *no scalar at all* — partial order over build/lint/test meta-signals (EvoGit).

**Three ledgers wandb has no primitive for** (all load-bearing):

- **Diff/patch archive with credit assignment** — per-edge `unified_diff(parent, child)` + metric deltas, LLM-labeled *imitation / avoidance / generalization / exploration* (GigaEvo), so "which change caused which delta" is queryable.
- **Distilled insight / experience ledger** — evidence-tagged lessons `{beneficial / harmful / fragile / rigid}` attached to lineage (GigaEvo), pooled-trace → reflection directives (GEA), "experience graphs" (Trellis, 2606.29823). **Critical constraint (Vesper): injecting raw history *hurts* under a token budget** — reading it costs tokens that would have bought more search. It's a **retrieval/distillation problem, not a logging problem.** (This directly echoes the tooling agent's finding that trace tools which only *store* don't help you *compare*.)
- **Tamper-evident integrity channel** — hack detections must live **outside the evolving artifact's reach**: DGM's agent *deleted the hallucination-detection markers from its own reward function*; Vesper's detector excluded **8.2%** of candidates from the strongest model; STOP measured sandbox-circumvention rate as an outer-loop metric.

**Git is a partial free implementation** (EvoGit, Vesper): commits = versions, branches = agents, three-way merge = crossover — genealogy, provenance, and reproducibility for free, with GitHub's network graph as a usable lineage UI. But git carries no metric vectors, archive semantics, selection state, or cost meters. **The wandb-analog is plausibly "git-native genealogy + a metrics / archive / ledger overlay + budget-indexed derived curves" — which is exactly the shape the NetHackers hub (digest-IDs, boards, register) is already converging on.**

---

## 7. Closest existing prior art (the map)

| System | What it already is | Where it stops |
|---|---|---|
| **HAL** (2510.11977) | cross-*harness* comparison infra: model × scaffold × benchmark, cost-controlled, Pareto frontiers, LLM "agent monitor" flagging cheating; runs on **W&B Weave** | a **leaderboard** (one row per (harness, model)) — no version lineage, seed-grouping, regression, or sweeps; batch campaigns, not live runs |
| **"Agentic Harness Engineering"** (2604.25850) | **nearest neighbor to NetHackers**: closed-loop harness *evolution* driven by three observability pillars (component / experience / decision); **every edit ships a falsifiable predicted-vs-actual contract** | single self-improving harness, not a general tracker |
| **OpenEvolve / ShinkaEvolve / AIDE** UIs | the only real "wandb-for-a-solving-process" UIs: evolution tree + MAP-Elites grid + code-diff + generating-prompt; ShinkaEvolve adds live genealogy, novelty-rejection rate, bandit posterior | welded to each project's internal structures; no shared log schema, no cross-run/cross-harness comparison, no trace linkage |
| **METR (Vivaria, RE-Bench, time-horizon)** | ops platform for *elicitation research*; **score-vs-time-budget curves**; success-vs-task-length is a *curve, not a point* | run browser + annotation, not metric-curve comparison across harness versions |
| **Inspect AI** | eval framework where the **Solver** *is* a scaffold abstraction; rich `.eval` logs | logs to *read*, not runs to *compare on curves*; cross-solver comparison is DIY |
| **Transluce Docent** | first tool whose unit of analysis is the *population of transcripts* — rubric-frequency over a corpus (found a harness bug that moved InterCode 68.6%→78%) | post-hoc analysis, no config/version identity, not live |
| **Weave / LangSmith / Langfuse / Braintrust / Phoenix / Opik / MLflow** | the trace + batch-eval layer, commoditized (OTel GenAI / OpenInference emit it) | "experiment" = dataset-snapshot eval; no step axis, no run/sweep over harness configs, no lineage. **MLflow is the one substrate with *both* halves in-house but doesn't fuse them.** |

---

## 8. What is genuinely new vs classic wandb (and what to build vs reuse)

Not everything ports. The parts with **no wandb primitive** — the actual research/product surface:

- **Budget-denominated steps with per-point CIs** (wandb assumes a free, dense, single global step; here steps are expensive, noisy, and hierarchical).
- **Artifact *genealogy*** — trees/DAGs, not linear version history; **archive heatmaps as first-class views**; the lineage *is* the primary index.
- **Cross-level aggregation** — game → mutation → generation → run roll-ups with group-by/drill-down.
- **Curve-point → OTel-trace linkage** as the drill-down terminus.
- **Distilled insight ledger** (retrieval, not dumps) + **tamper-evident integrity channel**.
- **Selection-signal computation** (CMP/subtree productivity, novelty, bandit state) shipped as derived metrics — the one feature that encodes the field's hardest-won lesson.

Reuse, don't rebuild: the **trace layer is commoditized** — emit OTel GenAI / OpenInference from L1 mutation episodes and any backend renders it. The **QD metrics + heatmaps are standardized** — pyribs/QDax already compute and draw them. The differentiated layer is the **run/process/lineage overlay over the evolution loop**, which today has *zero* standard log schema and *zero* general tooling.

---

## 9. Open questions this raises for NetHackers

- **What is the canonical progress functional?** BALROG-weighted progression is the obvious per-episode scalar; the run-level envelope is best-so-far + QD-score + coverage. But the archive's behavior descriptors are undecided — the code today is effectively **1-D categorical MAP-Elites** (elite pool keyed by character identity); a natural 2-D archive is **descent-aggression (max_depth/turns) × survival (turns)**, both already stored per atom.
- **Select on node score or clade productivity?** HGM says the former is quantifiably misleading. Adopting CMP-style selection is both an *algorithm* choice and a *what-to-log* choice — they're the same decision.
- **How much history to feed back?** Vesper's negative result says raw archive/history injection *hurts* per token — so the insight ledger is a retrieval problem to design, not a log to dump.
- **Where does the integrity boundary sit?** Hack detection and eval must be outside the evolving bot's reach (the DGM marker-deletion incident) — this couples the observability design to the sandbox/arena isolation already flagged as the #1 de-risk in the grounding note.

*Framing note: this document is a grounding/design-space map, not a build plan — the "what would it look like" question, answered. If/when you want to turn any panel or the outer-loop tracker into a spec, that's a separate step.*

---

## Sources

**Observability & experiment-tracking tooling.** W&B Weave (docs.wandb.ai/weave) & classic wandb Models/Sweeps/Artifacts · LangSmith · Langfuse · Braintrust · Arize **Phoenix** / **OpenInference** (spec/traces.md) · **OpenLLMetry**/Traceloop · **OpenTelemetry GenAI semantic conventions** (gen-ai-spans, agent spans; SIG since Apr 2024) · AgentOps (2411.05285) · Helicone · Comet **Opik** (+ Agent Optimizer) · **MLflow 3** GenAI · LangWatch (Scenario, Optimization Studio) · PromptLayer · Galileo · Patronus **Percival** / TRAIL.

**Comparing harnesses / benchmark infra.** **HAL — Holistic Agent Leaderboard 2510.11977** (hal.cs.princeton.edu; github.com/princeton-pli/hal-harness) · **AI Agents That Matter 2407.01502** · Towards a Science of AI Agent Reliability 2602.16666 · **SWE-bench 2310.06770** + SWE-agent 2405.15793 / Agentless 2407.01489 / AutoCodeRover 2404.05427 / SWE-Gym 2412.21139 · Anthropic "Raising the bar on SWE-bench Verified" · **τ-bench 2406.12045** (pass^k) · **METR** time-horizon 2503.14499 / HCAST 2503.17354 / RE-Bench 2411.15114 / Vivaria · **Inspect AI** (inspect.aisi.org.uk) · **GAIA 2311.12983** + Scaffold Effects 2606.08529 · Magentic-One 2411.04468 · AgentBench 2308.03688 · HELM · AppWorld 2407.18901 · Terminal-Bench · Galileo Agent Leaderboard v2 · Vals AI · **Same Signal, Different Semantics 2605.18332** · Beyond Resolution Rates 2604.02547 · Beyond pass@1 (reliability science) 2603.29231 · Efficient Benchmarking of AI Agents 2603.23749 · **Agentic Harness Engineering 2604.25850** · harness survey 2606.20683.

**Solving-process metrics.** **BALROG 2411.13543** · **NLE 2006.13760** + NetHack Challenge 2203.11889 · AgentBoard 2401.13178 · WebCanvas 2406.12373 · Crafter 2109.06780 · Voyager 2305.16291 · Let's Verify Step by Step 2305.20050 · Web-Shepherd 2505.15277 · **Large Language Monkeys 2407.21787** · HumanEval 2107.03374 · G-Pass@k 2412.13147 · **Cost-of-Pass 2504.13359** · ARC Prize 2024 2412.04604 · Tree of Thoughts 2305.10601 · LATS 2310.04406 · Mind Evolution 2501.09891 · GEPA 2507.19457 · Self-Consistency 2203.11171 · Semantic entropy (Farquhar et al., Nature 630, 2024) + probes 2406.15927 · Overthinking 2502.08235 · **TRAIL 2505.08638** · CoT monitoring 2503.11926 · Docent (transluce.org).

**QD / archive.** MAP-Elites 1504.04909 · QD (Pugh et al., Frontiers 2016) · **pyribs 2303.00191** (docs.pyribs.org) · **QDax 2308.03665** · CVT-MAP-Elites 1610.05729 · ELM 2206.08896 · QDAIF 2310.13032.

**Self-improving / evolutionary harnesses.** **DGM 2505.22954** · **HGM 2510.21614** (Metaproductivity–Performance Mismatch; CMP) · ADAS 2408.08435 · **SICA 2504.15228** · Gödel Agent 2410.04444 · STOP 2310.02304 · PromptBreeder 2309.16797 · **AlphaEvolve 2506.13131** · **FunSearch** (Nature 625, 2024) · EoH 2401.02051 · **ShinkaEvolve 2509.19349** · OpenEvolve (github.com/algorithmicsuperintelligence/openevolve) · **GigaEvo 2511.17592** (+ local [gigaevo-core-analysis](2026-08-10-gigaevo-core-analysis.md)) · **Vesper 2605.15221** · **EvoGit 2506.02049** · **GEA 2602.04837** · DemoEvolve 2605.24539 · Experience Graphs/Trellis 2606.29823.

**RL experiment-tracking discipline.** Henderson et al. "Deep RL That Matters" 1709.06560 · Agarwal et al. "Statistical Precipice" 2108.13264 (`rliable`) · Patterson et al. "Empirical Design in RL" (JMLR 25, 2024) 2304.01315 · Population Based Training 1711.09846.

**NetHackers internals referenced by the research agents:** `src/nethackers/arena/trajectory.py` (`TrajectoryResult`, `ResultStatus`) · `src/nethackers/hub/atoms.py` (`Atom`: seed, progression, milestone, ascended, turns, max_depth, identity) · `src/nethackers/hub/views/elites.py` (`elite_pool` keyed by character identity).
