# GigaEvo paper (arXiv:2511.17592) — representation & why-single-file

"GigaEvo: An Open Source Optimization Framework Powered By LLMs And Evolution Algorithms", Khrulkov et al., AIRI, subm. 2025-11-17. https://arxiv.org/abs/2511.17592 · html: https://arxiv.org/html/2511.17592 · code: AIRI-Institute/gigaevo-core.

## Bottom line: single-file is INCIDENTAL, not fundamental
- GigaEvo is explicitly a **reproducibility/infrastructure report**, not a method paper ("AlphaEvolve's high-level descriptions leave many implementation details unspecified, hindering reproducibility"). Choices are engineering pragmatics.
- **Representation is never justified — it's assumed**, inherited from the AlphaEvolve-style benchmark format (problem = self-contained dir: task_description.txt, metrics.yaml, validate.py, initial_programs/; candidate = a Python file with `entrypoint()`).
- The ONLY sentence on multi-file, in Limitations: *"The framework currently focuses on single-file Python programs; extending to multi-file projects and other programming languages remains future work."* → scope choice, not a search-theoretic requirement. No claim that search efficiency / credit assignment / crossover needs single-file.

## The ONE real coupling (this is what actually breaks at scale)
- Primary mutation operator = **whole-file rewrite**, chosen because *"many open-source models struggle to reliably produce syntactically correct diffs"*; rewrite "generates complete programs while carefully prompting the model to localize changes."
- Whole-file rewrite is only viable because programs are small. **At 13k LOC this is the piece that breaks** — and it's about the OPERATOR (rewrite vs diff), not the ARTIFACT (file vs tree).
- Crucial escape hatch for us: their diff-unreliability finding was *specifically open-source models*. Frontier models / coding agents don't share that limitation.

## Domains are single-file-shaped by construction
Heilbronn triangles (return 11×2 array), circle packing n=26/32, kissing numbers d=12, online bin-packing heuristic, prompt/agent evolution (Reddit-rules classification). All generate-a-construction / small-heuristic tasks copied from AlphaEvolve's suite. **No pre-existing codebase is ever edited** — evolution starts from `initial_programs/` seeds. NetHack (edit a large existing bot) is a different regime.

## Corrections to my earlier code-read
- "Evidence-tagged insights" is a code detail, not paper framing: insights are structured by type / effect(beneficial|harmful|neutral) / severity — no stated evidence/validation mechanism.
- **Crossover/multi-parent is NOT foregrounded** in the paper — "elites are sampled … fitness-proportional"; prompt takes "selected parent programs", cardinality unstated; the word "crossover" is absent. (The all-pairs multi-parent behavior is a code/config choice, not a paper thesis.)
- Lineage is emphasized as **bidirectional** (ancestor→me AND me→descendant analyses).
- **Multi-island MAP-Elites: null result** — "no clear benefit … contrary to expectations." Don't over-invest.

## Paper's own key components (4, from abstract/intro)
(i) MAP-Elites QD; (ii) async DAG-based evaluation pipelines; (iii) LLM mutation with insight generation + bidirectional lineage tracking; (iv) flexible multi-island strategies. "Implementation insights": rewrite>diff, bidirectional lineage, heterogeneous LLM routing ("Qwen for geometry, Gemini for discrete optimization"), Hydra config.

## vs AlphaEvolve
Borrows MAP-Elites, islands, machine-gradable eval, and the benchmark problems. **Rejects diff-first mutation** (open-source reliability) for whole-file rewrite with prompted locality. **Never adopts AlphaEvolve's EVOLVE-BLOCK marked regions.**

## Takeaway for NetHackers M3
Single-file gives NO reason to avoid a tree. What the paper's own logic says must change for a 13k-LOC tree:
1. Whole-file rewrite can't be the candidate-level operator → make the **file** (not the repo, not a hunk) the rewrite unit, OR use diffs with a frontier model. Candidate = the tree.
2. Insight/lineage stages currently read full program code + full parent→child transition → need file/subsystem-scoped diffs + summarized context.
3. Their eval assumes cheap deterministic scoring → NetHack episodes are stochastic/expensive (our variance work already accounts for this).
4. Don't over-invest in multi-island (their null result).
