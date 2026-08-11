# Guiding an autonomous open-ended search from above (guide, not co-pilot) — research

Question: how does a human GUIDE an autonomous evolutionary/QD loop's *policy* without per-step co-piloting?

## VERDICT
**Nobody successful puts a human in the mutation loop.** The human's proven role = author a small set of **standing artifacts** the autonomous loop consults every step — descriptors, evaluator code, an NL "interestingness policy," seeds, constraints, region weights — plus an optional low-frequency audit that refreshes them. Per-candidate human approval appears in **zero** successful systems.

## The proven knobs (all set-once, autonomous after)
1. **Behavior-descriptor choice = master steering knob.** The axes you pick determine what the search illuminates (MAP-Elites 1504.04909; Pugh et al. 2016; Rainbow Teaming 2402.16822 — human names "risk×attack" axes in NL once, judge-LLM measures, loop runs alone to >90% ASR). **Our (identity × progress-region) grid IS this knob.**
2. **Objective = code, judgment = LLM.** Scaled systems (FunSearch, AlphaEvolve, DGM, OMNI-EPIC) keep *fitness* as human-written machine-gradeable `evaluate()`/success-detector (anti-reward-hacking backbone); LLM judgment only for *interestingness/diversity* where hacking hurts less.
3. **NL interestingness works, set-once.** OMNI (2306.01711): a *fixed* few-shot prompt encoding "what's interestingly different" reweights sampling (boring ×0.001), beats learning-progress-only. OMNI-EPIC (2405.15568): same model-of-interestingness as a RAG accept/reject filter. QDAIF (2310.13032): NL axis prompts, 73% human agreement (95% on confident cases). Voyager: one sentence steers a whole run.
4. **Region priorities / selection weighting.** Go-Explore (Nature 2021): hand-set cell-selection weights were decisive. Per-cell weight multipliers = the "this region matters now" dial, applied per-step by the loop.
5. **Seeding.** ELM (2206.08896): the seed bounds the reachable space — seed each identity you care about or it may never emerge.
6. **Constraints / minimal-criterion gate.** POET (1901.01753) / Interactive Constrained MAP-Elites (1906.05175): admission bounds gate what enters the archive.
7. **Learned descriptors from preference BATCHES** (optional): QDHF (2310.12103) / DivHF (2310.06648) — contrastive descriptors from offline human similarity judgments; no per-step human.

## Failure modes of NL steering (all documented)
- **Judge reward-hacking at the top of the scale** — QDAIF: quality–human correlation collapses in the 0.995–1.0 band. Antidote: ground-truth *code* fitness; LLM only for softer signals.
- **Vague axes silently reinterpreted** — axes must be operational, not evocative.
- **Drift/Goodhart of the interestingness model** — OMNI warns explicitly; proposes periodic human recalibration (OEHF).
- **Archive-context truncation** → "learnable but meaningless" churn; fix = RAG-retrieve similar entries for the judge.

## Continuous vs one-shot
Set-once: descriptors, evaluator code, interestingness prompt, seeds, MC bounds, selection weights. Lightest *effective* refresh = **batch, between-epochs**: audit a sample of new elites → edit the policy / append few-shot judge exemplars (OEHF), or feed preference batches (QDHF), or inject one paragraph of textual feedback (Eureka). **Never per-step.**

## RECOMMENDED guidance surface for NetHackers — the "Search Charter"
A **versioned charter** (one artifact, git-distributed; contributors pin the same version — the role AlphaEvolve's problem spec plays):
1. **Charter file:** objective definition + interestingness rubric (~5–10 interesting/boring exemplars, OMNI's shape) + hard constraints + named-exploits blacklist + region priorities. Compiled into the interestingness/judge prompt + the mutation-agent brief context.
2. **Descriptor schema config:** axes, bins, per-cell **selection-weight multipliers** (the "this region matters now" dial; one-line commit, applied continuously).
3. **Seed elites per identity** you care about.
4. **Minimal-criterion gate** before archive admission: compiles, survives N turns, no crash, not on the exploit blacklist.
5. **Interestingness filter on mutations** (OMNI-EPIC): before spending eval budget, retrieve k most-similar archive entries, ask judge "interestingly different or trivial variant?" — defense against meaningless churn across distributed contributors.
6. **Audit cadence (OEHF):** periodically sample new elites, human rates, ratings become new charter exemplars. The ONLY recurring human duty.

## Mapping to our design (important simplifications)
- Our design = **AlphaEvolve/DGM's program-evolution loop wearing OMNI's steering stack.** Every needed mechanism has a validated donor.
- **Progress-region axis** = code-measured (our `progress.py` 87 milestones / BALROG metric). Hard metric, aligned with fitness — best-case descriptor.
- **Identity axis**: for us this is *deterministic* — the character is fixed by the objective batch, not inferred. So we may need **no LLM judge for descriptors at all** (cleaner than the general case; LLM-as-judge only if we later add a fuzzy "playstyle" axis).
- **Fitness** = NetHack ground truth (progression/depth/survival/conducts from game logs). Do NOT let an LLM score fitness.
- **Charter-as-versioned-artifact** also solves distributed coordination: everyone pins the same policy version.

Key sources: OMNI 2306.01711 · OMNI-EPIC 2405.15568 · QDAIF 2310.13032 · QDHF 2310.12103 · DivHF 2310.06648 · Rainbow Teaming 2402.16822 · Intelligent Go-Explore 2405.15143 · DGM 2505.22954 · AlphaEvolve 2506.13131 · Eureka 2310.12931 · Go-Explore Nature 2021 (1901.10995) · POET 1901.01753 · ELM 2206.08896 · Voyager 2305.16291.
