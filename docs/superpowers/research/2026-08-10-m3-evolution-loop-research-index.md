# M3 evolution-loop research — index (2026-08-10)

Research batch grounding the M3 design (the distributed evolution loop). Gathered while brainstorming "what does the LLM evolve / how does the loop work / how do we guide it." All files dated 2026-08-10.

## Prior art — the code we adapted from
- **[gigaevo-core-analysis](2026-08-10-gigaevo-core-analysis.md)** — the LLM-evolution engine (AIRI). Candidate = one whole Python file; MAP-Elites multi-island over Redis; mutation+crossover = one prompt with an archetype menu; crossover = feed 2 parents (no AST merge); domain seam = a problem *directory*. Single-node; `merge_programs` is the distribution seam.
- **[nethackers-v1-disposable-prototype-analysis](2026-08-10-nethackers-v1-disposable-prototype-analysis.md)** — our thrown-away first attempt. Evolved a git diff (against a pinned 15-file AutoAscend whitelist) wrapped in a dict wrapped in a carrier `.py` → **triple indirection = disposability reason #1**. ~7k LOC + ~2k LOC of vendor adapters + anti-cheat-before-users. Keep list: digest-as-ID, deterministic seeded eval, progress-shaped fitness, viral README.
- **[gigaevo-platform-architecture](2026-08-10-gigaevo-platform-architecture.md)** — AIRI's distributed orchestration (master/runner, Kafka, Postgres, MinIO). Unit of work = a whole *experiment*; central push scheduler; the dormant "evolutions" API sketches register→pareto-on-read→promote.
- **[gigaevo-platform-purpose-vs-nethackers](2026-08-10-gigaevo-platform-purpose-vs-nethackers.md)** — myth-buster: the platform is an **AutoML / prompt / LLM-chain optimization** product, not general program evolution. Its evolutions API is a **convergent-design** data point for our hub shape, NOT operational validation (never wired, single-writer).

## The representation question — why GigaEvo uses one file
- **[gigaevo-paper-representation](2026-08-10-gigaevo-paper-representation.md)** — the paper is an infra report; single-file is **incidental** (a Limitations line: multi-file = future work). The real coupling is *whole-file rewrite* (chosen because open-source models can't emit reliable diffs) — an operator limit, not an artifact one. No pre-existing codebase is ever edited.
- **[alphaevolve-representation](2026-08-10-alphaevolve-representation.md)** — the reference. Evolves **human-marked regions** (`EVOLVE-BLOCK`) via SEARCH/REPLACE **diffs** inside a **frozen skeleton** that preserves the `evaluate()` contract. "Entire codebase" = many marked blocks (≤ hundreds LOC), not tree regeneration. The real variables are *frozen skeleton / mutable surface / operator* — not file-vs-tree.

## Is our design grounded? / how do we guide it?
- **[agentic-evolutionary-search-grounding](2026-08-10-agentic-evolutionary-search-grounding.md)** — verdict: **novel-but-plausible, converging on grounded.** Coding-agent-as-operator (DGM, SATLUTION, Vesper, AVO, RHO) is validated and *beats* single-completion; whole-repo-tree evolution is validated (SATLUTION/RHO); *agentic-operator × true MAP-Elites* + *volunteer-distributed* is the open cell we'd occupy. Bites: reward-hacking (#1 failure), noisy-fitness archive poisoning, thin crossover, hands-free fragility. Thesis: BALROG shows LLMs can't *play* NetHack (1.57%) but can *engineer* bots beyond human level — the arbitrage.
- **[guiding-autonomous-search](2026-08-10-guiding-autonomous-search.md)** — nobody successful puts a human in the mutation loop. Guidance = set-once artifacts (descriptors = master knob, code-fitness, NL interestingness, region weights, seeds, constraints) + periodic elite audit. (Superseded in the live design: locality-of-exploration via `search`+`boards` replaces a hosted "charter" for now.)

## Cross-cutting conclusions feeding the M3 design
1. **Artifact = the whole solution tree; operator = a coding agent editing it** (grounded, and better than single-completion). Frozen skeleton = the M2a arena/entrypoint contract.
2. **Single-file was never load-bearing** — it proxied "bound the mutable surface + freeze the evaluator contract," which a tool-using agent handles by navigating the tree.
3. **Non-negotiables from the field:** sandbox/isolate mutator from evaluator; multi-seed *revalidate* before an elite displaces the incumbent; select parents by lineage productivity (HGM); ship *distilled insights*, not raw dumps (Vesper: maximal context hurt).
4. **Coordination stays thin & decentralized** — hub shows the frontier (`boards`) and serves parents; contributors self-direct via `search` (locality of exploration). No central `next()`/scheduler, no hosted charter at MVP.
5. **Archetypes:** modest evidence (EoH/ShinkaEvolve, single-completion only) — keep a *small* menu, add a bandit only if signal appears. Not a foundation.
