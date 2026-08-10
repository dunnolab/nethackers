# AlphaEvolve (arXiv:2506.13131) — representation & why NOT whole-artifact

DeepMind, 2025. "A coding agent for scientific and algorithmic discovery." PDF page refs below.

## Edit unit = human-marked regions, mutated via SEARCH/REPLACE diffs
- User annotates an existing codebase with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END`. Everything outside is an **immutable skeleton** that "ties the evolved pieces together, so that they can be invoked from `evaluate`." (p.5)
- LLM outputs a *sequence of diff hunks*: `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE`; `child = apply_diff(parent, diff)`. **AlphaEvolve never emits the whole artifact.**
- Whole-block emission is a config option only "where the code being evolved is very short, or when a complete rewrite is more appropriate." ← that small regime = exactly GigaEvo's single-file case.

## "Entire codebase" = many marked blocks, NOT tree regeneration
- Table 1 demonstrated ceiling: "evolves entire code file / up to hundreds of lines of code." **Nothing near 13k LOC is ever in the mutable surface.**
- Big-system wins (Borg scheduler, Gemini kernels, TPU Verilog, XLA/FlashAttention IR) all evolve a **bounded component inside a fixed harness**, each mutant "checked against the reference (unmodified) code."
- "Flexibility in choosing the abstraction" (p.5): what you evolve (solution string / constructor fn / search algorithm / co-evolve) is a free design choice; "different levels of abstraction work better for different problems."

## FunSearch → AlphaEvolve (Table 1)
single function → entire file; 10–20 LOC → hundreds; millions of small-LLM samples → thousands of SOTA-LLM samples; minimal → rich context; single → multi metric. Enabling change = **SOTA LLMs + diffs** ⇒ few, smart, targeted mutations instead of mass whole-function resampling.

## Machinery
Async controller: `parent, inspirations = db.sample(); prompt = build(...); diff = llm.generate(prompt); child = apply_diff(parent, diff); results = evaluator.execute(child); db.add(...)`. DB = MAP-Elites + island models. Prompt = prior programs + scores + explicit context (docs/PDFs) + stochastic formatting + meta-prompt evolution. Evaluator = user `evaluate() -> dict[str,float]`, cascade (prune early), optional LLM feedback, parallel (~100 compute-hrs/candidate feasible), multiobjective ("optimizing multiple metrics often improves the single target"). Ensemble = Gemini Flash (throughput) + Pro (breakthroughs).

## WHY bound the surface — causal chain (explicit in the paper), in order of force
1. **Evaluator-contract preservation** — skeleton exists "so that they can be invoked from `evaluate`"; every mutant across a thousands-deep lineage must stay runnable/scoreable. Whole-artifact emission at scale silently breaks interfaces the evaluator needs.
2. **Grounding/hallucination control at long horizons** — code + programmatic eval "allows AlphaEvolve to carry on the evolution process for a large number of time steps."
3. **Sample economics** — thousands-not-millions ⇒ each generation must be short/cheap/targeted; exact-match SEARCH gives **per-hunk rejection**, not per-artifact rejection.
4. **Credit assignment / population coherence** — child differs from parent by an inspectable diff.

## The counter-force (don't over-bound)
Ablation "No full-file evolution" (evolve only the loss fn) is **significantly worse** than evolving all blocks. ⇒ **Bound the surface, but make it WIDE enough to span every component the search must co-adapt.**

## The gap both papers leave (our opportunity)
AlphaEvolve gives **no mechanism for discovering which regions to mark** — "that curation is the human's job." Automating/steering that is the novel part. Honest caveat: demonstrated mutable surface never exceeds ~hundreds of LOC; many-regions-across-13k-LOC simultaneously is beyond what the paper evidences.

## Synthesis for NetHackers M3
The real design variables are NOT file-vs-tree. They are:
1. **Frozen skeleton** = the eval harness + entrypoint/interface contract. *We already have this* (M2a arena: `--solution /sol`, fixed `bot.py` entrypoint, fixed NLE + ObjectiveSpec batch). A candidate that breaks it scores is_valid=0 — the evaluator catches it, exactly as AlphaEvolve relies on.
2. **Mutable surface** = which part of the bot the search may touch. Both papers say bound it; AlphaEvolve says keep it wide enough to co-adapt. THIS is the genuine design work for us.
3. **Operator** = diff vs rewrite, at what granularity. GigaEvo's diff-failure was *open-source models*; we can use frontier coding agents, so diffs/targeted edits are on the table.
**Modern twist neither paper had:** a coding agent (grep/read/edit) navigates a large frozen tree and edits a bounded region *surgically*, choosing the region itself — instead of a human pre-marking blocks for a single-completion diff. So: **artifact = tree; skeleton = M2a harness (frozen); operator = agent-driven targeted edit; diff = derived (lineage/credit), not emitted blind (v1's mistake).** The open question becomes how to bound/steer the per-mutation surface.
