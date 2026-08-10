# gigaevo-core analysis (for NetHackers M3)

Source: `~/nethacker-evolution/nethackers/vendor/gigaevo-core` (fork of AIRI-Institute/gigaevo-core).

## What the LLM evolves — THE answer
A candidate = **one whole Python source file** (`class Program(BaseModel).code: str`, `programs/program.py:56`). Plus `metrics: dict[str,float]`, `metadata` (pickled), `lineage{parents,children,mutation,generation}`, `state`, `atomic_counter`. Stored as JSON at Redis `{prefix}:program:{uuid}` + per-status sets + event stream.

LLM output modes: **rewrite** (default; whole replacement file, any #parents) or **diff** (unified diff via `diffpatch`, exactly 1 parent). `llm/agents/mutation.py:162-264`.

## Loop shape
Single OS process, two asyncio tasks coordinating ONLY via Redis program-states:
- `DagRunner` (`runner/dag_runner.py`): picks up FRESH programs, runs an eval DAG → `DAG_PROCESSING_COMPLETED`. Semaphore `max_concurrent_dags=8`.
- `EvolutionEngine.step()` (`evolution/engine/core.py:170`): await-idle → `strategy.select_elites` (default 5/gen) → `generate_mutations` (default 8/gen, spawn FRESH children) → await-idle → ingest (acceptor → `strategy.add` MAP-Elites cell competition, else DISCARDED) → flip EVOLVING→FRESH so lineage/context stages recompute.
Config: `max_elites_per_generation:5`, `max_mutations_per_generation:8`, `num_parents:2`, `mutation_mode:rewrite`.

## Mutation / crossover engine
One prompt for BOTH mutation and crossover (`prompts/mutation/{system,user}.txt`). Fed per parent: full code fence + `metadata["mutation_context"]` = formatted metrics + tagged insights + family-tree lineage with metric deltas. User prompt offers an **archetype menu**: exploitation(1-3)/exploration(4-6, incl. #6 "Approach Synthesis — combine multiple techniques")/hybrid(7-8), with plateau-triggered exploration + no-repeat-harmful rules.

**Crossover = NO AST merge.** It's the same mutation call with `num_parents:2` + `AllCombinationsParentSelector` (all elite pairs) → two full parent blocks in one prompt; LLM fuses free-form; child records both parents. Complementarity emerges because elites come from different cells/islands. = **GigaEvo idea #3 "combine complementary regions"**.

## Archive
**Multi-island MAP-Elites over Redis** (`MapElitesMultiIsland`). Behavior descriptors = chosen program *metric keys* binned by `BehaviorSpace` (linear/log/sqrt). Default islands differ in behavior space + selection pressure: `fitness_island` (primary×150 + is_valid×2) vs `simplicity_island` (primary×20 + complexity×10, evicts most complex). One program per cell; replace iff better (weighted-sum or Pareto selector). Migration every 25 gens. Elites for breeding = per-island quota → pooled → sampled.

## The 5 GigaEvo ideas in code
1. **Evidence-tagged insights**: per-program LLM insights `{type,tag∈beneficial/harmful/fragile/rigid,severity,evidence}` → injected into mutation prompt with action semantics (preserve/remove/robustify).
2. **Lineage credit-assignment**: `difflib.unified_diff(parent,child)` + metric deltas → LLM labels strategy (imitation/avoidance/generalization/exploration) → feeds future mutations. EVOLVING→FRESH refresh keeps it current.
3. **Multi-parent synthesis** (crossover above).
4. **QD archive**: islands w/ different behavior spaces + selectors + migration = diversity engine.
5. **Heterogeneous LLM ensemble**: `MultiModelRouter` samples models by probability per call. (+ optional LLM-as-judge `llm_score` stage → extra behavior dim.)

## Domain plug-in seam — how NetHack would attach
NOT a class — a **problem DIRECTORY contract** (`problems/context.py`, `problems/layout.py`):
```
problems/<name>/
  task_description.txt      # objective → system prompt
  metrics.yaml              # exactly ONE is_primary; mandatory is_valid∈[0,1]
  validate.py               # validate(payload[,context]) -> dict[str,float]
  initial_programs/*.py     # ≥1 seed, each exposes entrypoint(...)
  context.py (optional)     # build_context() -> dict passed to entrypoint & validate
```
Eval DAG: ValidateCode → CallProgramFunction("entrypoint") runs program.code in an isolated subprocess (`tools/exec_runner.py`, RLIMIT_AS 16GB, 600s) → payload → CallValidatorFunction → metrics → acceptor/behavior/fitness. Or replace executor stages with a custom `Stage` subclass.

## Concurrency & distribution posture (KEY for our hub)
Single process, pure asyncio; heavy user code offloaded to short-lived subprocesses. Distribution seam: storage enforces **single-writer per Redis prefix** (`__instance_lock__`), has a **`read_only`** mode, and an additive **`merge_programs`** (`database/merge_strategies.py`) — union children, dict-merge metrics/metadata tie-broken by `atomic_counter`, `code` immutable, state-lattice merge. That merge is the natural seam a distributed thin-hub adaptation would build on.

## M3 implications (my notes)
- GigaEvo's "program = whole Python file scored by a validator" is elegant for small self-contained programs. For NetHack the "program" is a full AutoAscend-based bot — orders of magnitude bigger than one file. **The central M3 question is exactly how v1 mapped that big artifact onto gigaevo's one-file-Program model** (v1 partial finding: candidates injected as generated Python files returning a dict).
- The prompt-level, archetype-driven mutation+crossover with lineage/insight context is the transferable heart. Whether we keep gigaevo-core as a dep or reimplement a lean version is an open M3 decision.
- MAP-Elites descriptors = metric keys → maps cleanly onto our hub's per-identity/behavior boards.
- Redis single-writer + merge_programs is heavy; our thin hub (SQLite + register) is the intended replacement for the shared archive.
