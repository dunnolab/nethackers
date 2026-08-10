# gigaevo-platform: what is it actually FOR? (myth-busting the "validates our shape" claim)

## VERDICT: the AutoML hypothesis was RIGHT; "validates our shape" was OVERSTATED
gigaevo-platform is an **in-house AutoML/prompt/agent-optimization service**, not a general program-evolution registry. README.md:3: *"A machine learning experiment management system with a microservices architecture."*

## What it evolves (all dataset-scored ML/NLP artifacts)
- **Tabular AutoML**: feature-selection programs scored by LightAutoML/CatBoost. `classification_automl/task_description.txt`: *"You are evolving a feature selection program … maximize downstream F1 (macro) of an AutoML classifier (LightAutoML)."* Datasets: titanic/iris/boston/california/wine/… (canonical AutoML set).
- **Prompt-template optimization**: `prompt/task_description.txt`: *"PROMPT EVOLUTION. You are evolving a prompt template string…"* on gsm8k/xsum/sentiment. The "best program" = the prompt string.
- **CARL "chains"**: multi-step LLM reasoning pipelines. An "individual" = `IndividualCreate.chain_content: Dict[str,Any]` — a JSON DAG of prompt-steps (`stage_action`, `reasoning_questions`, deps), NOT code. Fitness = a **natural-language judge prompt scored by an LLM** (`FitnessSpec.prompt` + judge `model_id`).

The general engine is one level down (`gigaevo-core`, EVOLVE-BLOCK + validate.py, AlphaEvolve-family); the *platform* productizes it only for these fixed ML/prompt/chain templates.

## The "evolutions" API — dormant scaffolding
`evolution_service.py:8-12`: *"This service deliberately does not start a chain experiment yet — that wiring is a separate item in TODO.md (the evolution loop)…"* No worker/scheduler/Kafka consumer touches it. Records = MinIO JSON blobs (`evolutions/<id>.json`), not even a Postgres table (`init.sql` has experiments/runner_instances/tasks only). It's a front-porch for the external **CARE** TUI client. `gigaevo-memory` = "persistent memory for CARL artifacts (steps/chains/agents/memory-cards)" with `latest`/`stable`/`evolved` channels — a versioned artifact store for LLM agents, i.e. AutoML-for-agents infra.

## Honest NetHackers comparison
| Axis | GigaEvo Platform | NetHackers |
|---|---|---|
| Purpose | in-house AutoML/prompt/chain optimization (one org) | open distributed evolution of one game-playing program |
| Evolved unit | prompt strings; chain JSON; small feature-select fns; fitness = dataset metric or **LLM-judge** | ~13k-LOC symbolic program tree; fitness = objective NetHack episodes |
| Compute | **platform-owned** runner pool, platform-paid LLM keys | **BYO** — contributors' own compute; hub runs zero |
| Coordination | central **push** (Kafka, master assigns runners via docker.sock) | **pull** registry; no scheduler |
| Archive | per-run MinIO blobs, pareto-on-read, accept ONE winner/run, archive delegated to gigaevo-memory; no leaderboard/identity | persistent multi-contributor: leaderboard + elite pool + attainment; `register` = first-class multi-party write |

## What transfers vs. doesn't
**Transfers (convergent design):** the record schema (per-objective `fitness_scores` validated against declared `objectives`, `parent_ids`/`mutation_kind`/`generation` lineage), **pareto-on-read** (real O(n²) non-dominated filter at read time), dumb-blob persistence + derived views, and **accept/promote** as an explicit transition with channel semantics. That IS our register/leaderboard/elite-pool skeleton — independently sketched by another team ⇒ the pattern is *natural*.
**Does NOT transfer:** any evidence it works (unwired — "TODO"); multi-contributor write path (none — single trusted writer); archive-over-time (single-run/single-winner; persistence outsourced); trust/identity/anti-gaming (none); payload economics (chain JSON vs program trees); and the *shipped* half is architecturally opposite (central push + platform compute).

## Correct phrasing for the design doc
> "gigaevo-platform's dormant evolutions API independently sketches the same register + pareto-on-read + promote skeleton for a different domain (LLM-chain GA), which supports the pattern's naturalness — it does NOT demonstrate the pattern operating, and it solves none of our multi-party trust problems."

⇒ Alignment-row fix: "Distributed thin-hub coordination" = **NEW / convergent-design (not operational validation)**; the real distributed precedent is git-native **EvoGit**, not this.
