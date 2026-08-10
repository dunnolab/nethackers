# NetHackers M3 — Distributed Evolution Loop (design)

**Status:** MVP design, approved in brainstorming (2026-08-10). Builds on merged **M1** (arena + local eval) and **M2a** (hub: atoms substrate + attainment/elite-pool/boards views + objective catalog + `register` + rich CLI). Implementation not yet started.

**Goal:** Turn the hub into a live flywheel. A coding-agent-driven **harness** pulls an elite program for an objective, improves it, evaluates it locally, and registers it back — climbing the boards toward reliably ascending NetHack. **MVP success = one real, held-out-confirmed improvement over the AutoAscend baseline on a single objective.**

**Architecture (one line):** an **AlphaEvolve-style MAP-Elites evolutionary algorithm with a coding-agent mutation operator**, spread across a thin shared archive (the hub) and many local operators (harnesses on BYO compute).

**Tech stack:** reuse `nethackers` (contracts, arena `eval_batch`, hub store/API/CLI, hubclient). New code = the harness loop + a pluggable headless coding-agent operator. No Redis/Kafka/Hydra/LangChain (explicitly avoiding the disposable-v1 and gigaevo-platform machinery).

---

## 1. Framing & prior art

The loop is the **standard MAP-Elites evolutionary algorithm** — `select → mutate → evaluate → insert` — with exactly **one substitution vs AlphaEvolve/GigaEvo**: the mutation operator is a **tool-using coding agent editing a program tree**, not a single LLM call emitting a diff. This is the agentic-operator line validated by Vesper / RHO / AVO (see grounding refs). Everything else is proven machinery.

Key framings settled in brainstorming:

- **Artifact = the whole solution tree** (M2a's solution-root model), *not* a single file or a diff-in-a-dict. Single-file in GigaEvo was incidental (a Limitations-section scope choice); the real constraint is bounding the mutable surface + freezing the evaluator contract — which a coding agent handles by navigating the tree. This deletes the disposable-v1's triple-indirection failure class.
- **Niches = objectives.** M2a already stores objectives and computes per-objective elites/boards, so the MAP-Elites archive *is* the existing per-objective elite pool. No new grid, no region axis to invent.
- **Objective-dependent loop.** A loop is started against **one objective**; that objective fixes the parent pool, the fitness, the eval batch, and the board. Locality = the contributor's choice of objective.
- **Generalist vs specialist is handled by niching, not by hoarding losers.** A specialist is the *champion of its own niche*; a generalist is the champion of a broad objective (`all`/`random`). Sampling per-objective elites already gives specialist+generalist coverage (equivalent to RHO's Pareto-coverage at the objective grain). Losers are discarded; champions are kept.
- **Decentralized coordination.** The hub *shows* the frontier (`boards`/`search`) and serves parents; it never *assigns* work. No central `next()`, no scheduler, no hosted "charter" at MVP. Coverage emerges from many contributors' local choices.

## 2. The shape

- **Hub = the shared MAP-Elites archive** (thin: FastAPI + SQLite, **zero compute, zero LLM**). Stores every registered program; keeps each niche's champion; serves reads (`search`, `boards`, `elites`, fetch-a-tree) and the `register` write. All M2a, lightly extended.
- **Harness = the operator loop** (local, BYO compute + BYO coding agent). Runs `select → mutate → evaluate`, then registers. All new.

## 3. The loop (four operations)

### SELECT
- Aimed at **one objective** (harness config: `evolve <objective>`).
- Parent = **the objective's top elite** (MVP: exploit — take the best; explore-sampling from top-k or a neighbouring niche deferred).
- **Cold start:** an objective with no elite yet seeds from the **AutoAscend baseline**.
- Single parent at MVP; **influences** (donor elites from other niches) deferred.
- Hub role: pure reads. The exploit/explore policy lives in the harness.

### MUTATE (the substitution)
- Harness makes an **isolated working copy** (git worktree) of the parent tree; the coding agent edits it in place.
- **Lean brief** (curated, not maximal — Vesper found maximal context hurts): the objective's task description + the parent's **scorecard** (score + where it fails) + one rule, *"don't break the `bot.py` entrypoint."* MVP intent = *"improve this bot on this objective."*
- **Sealed from the evaluator:** the agent sees only the working copy — never the eval seeds or scoring code.
- **Budget = token cap** (soft: accumulate per-step usage, stop after the step that crosses it; overshoot OK), plus a **wall-clock timeout** backstop for a hung agent. Tokens-per-candidate are recorded regardless.
- Output = the edited tree (child, new digest) + the captured **diff** (for lineage; not emitted by the agent).

### EVALUATE
- **Cheap gate first:** imports · exposes the entrypoint · differs from the parent · survives a short smoke without crashing. Broken mutants die here for ~nothing.
- **Real eval:** `eval_batch` (the M1/M2a arena) on the objective's **dev** seed batch, in Docker with **no network**. Fitness = **mean progression** (0–1), ascension as the ceiling.
- **Seed split:** each objective's seeds are a **dev batch** (scored here) and a reserved **held-out** set (used only in INSERT). This split is what makes "improvement" mean *real* improvement, not seed-overfitting or a lucky run.
- Output = fitness + per-episode **evidence** (M2a `Evidence`).

### INSERT
- **Revalidate a would-be winner:** if the child beats the incumbent on the **dev** batch, re-score on the **held-out** seeds (larger N; modest at MVP, tune toward N≈33). Losers skip this. Compare **held-out vs held-out**.
- **Register** (M2a path, lightly extended): child tree + evidence (dev + held-out) + lineage (parent digest, diff, tokens spent). Evidence tier = self-reported.
- **Compare + place (hub, pure data):** if the held-out score really beats the objective's elite, the child becomes the new elite; boards/attainment update.
- **Retention:** keep each niche's **champion(s)** (top-k); **discard the rest** — clear losers aren't registered at all. Specialists survive because they're champions of *their* niche, not because losers are hoarded.

Loop → back to SELECT.

## 4. The coding-agent operator

- **Pluggable, two headless backends at MVP: Codex (`codex exec`) and Claude Code (`claude -p`).** The harness picks whichever the contributor configured/has.
- Invocation: run the agent **headless** with cwd = the worktree, the brief as the prompt, sealed from eval inputs, capped by the token budget. Read token usage from the backend's per-step reporting.
- **No HTTP/OpenAI-emulation shim** (the disposable-v1 mistake): the coding agent *is* the operator, invoked directly as a subprocess.
- Recording which backend produced a candidate gives cheap operator diversity/comparison (a nod to GigaEvo's heterogeneous-model idea, at the agent level).

## 5. Data & hub deltas

M2a already provides: the atoms substrate + solution/lineage store, `register`, the objective catalog + published batches, and the `elites`/`boards`/`search`/`attainment` views. M3 adds only:

- a **dev/held-out seed split** per objective (a property of the published batch; the held-out set is reserved, not scored on the public board),
- **lineage capture** on register: the parent digest, the diff, tokens spent, and the operator backend (light extension of the existing lineage write),
- a **fetch-a-tree** primitive for the harness to pull a parent's files (may already be M2a's `pull`).

Everything else the hub does at MVP is existing reads/writes.

## 6. Reuse vs. new (the delta)

**Reuse (built):** arena `eval_batch` (Docker, no-net) · hub store/API/CLI (`register`, `elites`/`boards`/`search`/`attainment`, objectives) · the registered AutoAscend seed.

**New (the whole delta to build):**
1. the harness loop orchestration (`select → mutate → evaluate → insert`) on BYO compute,
2. the pluggable headless operator (Codex + Claude Code) invoked in a worktree with the lean brief,
3. token soft-cap + timeout (+ token logging),
4. the cheap gate,
5. the dev/held-out seed split + held-out revalidation,
6. light lineage capture on register.

## 7. Scope boundary

**In (MVP):** single contributor · one objective · single parent (exploit) · cold-start from AutoAscend · lean brief · sealed operator (Codex + Claude Code headless) · token budget · cheap gate · arena eval on dev seeds · dev/held-out revalidation · register winners, discard the rest.

**Deferred (discussed, explicitly out of MVP):** explore-sampling / bandits · **influences & agent-mediated crossover** · LLM-distilled insights & lineage in the brief · archetype menu · exploit blacklist · multi-metric fitness · **hub-side trust-verification / re-running evals (M2b)** · archive pruning/capacity · multi-objective (one child → several niches) placement · **distribution across many contributors** · multi-identity breadth · the guidance "search charter" · playstyle descriptors · interestingness filtering.

## 8. Success criteria

- **Pass:** ≥1 child is registered that beats AutoAscend on **held-out** seeds for the chosen objective → the flywheel turns; then scale (more objectives, then contributors).
- **Fail:** none does → the operator / brief / eval needs rethinking *before* any scaling. Cheap to learn now.

## 9. Risks & how the design handles them

- **Reward/eval hacking (the #1 reproduced failure in the field):** the operator is **sealed from the evaluator**; the eval runs in a sandbox (`--network none`); "improvement" is confirmed on **held-out** seeds the agent never optimized against. (Hub-side re-run verification is deferred to M2b; this is local-loop hygiene, not contributor anti-gaming.)
- **Noisy / heavy-tailed fitness poisoning the archive:** multi-seed eval + **held-out revalidation before an elite is displaced**; N tuned toward the variance-sized ~33.
- **Fully hands-free fragility:** the cheap gate rejects broken mutants; the token budget + timeout bound cost; the success bar is falsifiable and small.
- **Operator cost economics (deep vs shallow per candidate — Vesper):** tokens-per-candidate are logged from day one, so the budget policy can move from a fixed cap toward tuned economics without changing the loop.

## 10. Grounding

See `docs/superpowers/research/2026-08-10-m3-evolution-loop-research-index.md` and the eight reports it indexes — especially `agentic-evolutionary-search-grounding` (Vesper/RHO/AVO/DGM validate the agentic operator + repo-tree evolution), `alphaevolve-representation` and `gigaevo-paper-representation` (why single-file was incidental), and `guiding-autonomous-search` (why no per-step human, why niching/descriptors are the steering knob). Plus prior M-series research on eval cost/variance and progress measurement.
