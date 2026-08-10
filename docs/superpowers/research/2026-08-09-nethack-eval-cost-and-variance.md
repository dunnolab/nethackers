# NetHack evaluation: what an episode costs, how noisy it is, and which evaluation regimes are actually feasible

**Date:** 2026-08-09
**Status:** empirical analysis (no design prescriptions)
**Question:** How expensive and how noisy is evaluating a symbolic NetHack bot — and therefore which evaluation regimes (episodes per candidate, episodes per registered result, per-identity N, full-catalog runs) are feasible for a laptop-class contributor and for the hub?

Every number below is tagged:

- **[measured]** — measured for this report, on this machine (Apple M4 Max, 16 cores, 48 GB RAM, macOS 15.5, Python 3.11, `nle==1.3.0`), through the project's *real* evaluation path: `nethackers.arena.environment` (`NetHackChallenge`, full public observation keys, autopickup off, `fix_moon_phase`) + `nethackers.arena.sandbox.AgentClient` (spawned bot subprocess, per-step pipe IPC) + the `roots/autoascend` solution. Raw episode records (96 full episodes + probes) and the benchmark/statistics scripts are preserved in [`data/2026-08-09-eval-cost/`](data/2026-08-09-eval-cost/).
- **[cited]** — pulled from a published source (URL given).
- **[derived]** — arithmetic on measured/cited numbers; the derivation is shown.

---

## 0. TL;DR

| Quantity | Value |
|---|---|
| Raw NLE env stepping (project env config, 1 core) | **~20–23k steps/s** [measured]; 14.4k steps/s on a 2.9 GHz i7 MacBook [cited, NLE paper] |
| AutoAscend through the full sandboxed arena path | **~1.2–1.9k steps/s** (solo ~1.5–1.9k; 12 parallel episodes ~1.1–1.3k) [measured] |
| numba JIT warmup | **22 s once** (cold cache); **0.7 s** warm startup per episode thereafter (cache persists across processes/runs) [measured] |
| Full episode wall-clock (random `@`) | **mean 23 s, median 22 s** (mean 26k steps) [measured]; strong identity (Valkyrie) mean 34 s (mean 43k steps) [measured] |
| Machine throughput | **~0.30 episodes/s ≈ 1,100 episodes/hour** at 12 workers on M4 Max [measured]; est. **~300–400/h** on a 4-core 2017-i7-class laptop [derived] |
| Memory | ~350 MB per concurrent episode (bot child 295 MB + driver 44 MB) [measured] |
| Episode-outcome noise (fixed bot + fixed identity) | progression **CV ≈ 0.58** (mean 0.116, sd 0.067, val-dwa-law-fem, n=32) [measured]; random-draw CV ≈ 0.47 [measured]; in-game score CV ≈ 0.65–1.27 [cited/derived] |
| Same-seed pairing benefit for A/B comparisons | **≈ none once behavior diverges** — outcome correlation under a 1/2000 action perturbation: ρ ≈ −0.3 (n.s., n=16) [measured]. Determinism gives exact zero-diff on seeds where behavior does not diverge [measured] |
| N for ±10% SE on mean progression (one identity) | **≈ 33 episodes** [derived from measured sd] |
| N to detect +0.01 progression (~9% relative) | **≈ 700/arm** — out of reach of inner loops; +0.05 (~43%) needs ~28/arm [derived] |
| N for ascension-rate work | witness a 1% rate: **~300**; 0-in-300 proves p<1% (95%); distinguish 1% vs 5%: **~284/arm**; 1% vs 2%: **~2,300/arm**; measure 1% to ±10%: **~9,900** [derived] |
| The Challenge's magnitudes | dev: 512 episodes / 2 h; final: 4,096 episodes / 24 h per submission, AWS g4dn.xlarge (4 vCPU) [cited] — 4,096 puts a 95% upper bound of **0.07%** on an unobserved ascension rate and ±1–2% relative SE on mean score [derived] |

---

## 1. Episode cost

### 1.1 The environment is nearly free; the bot dominates

- Project env (`DeterministicChallenge`, all 14 public observation keys): **19.6k–22.8k steps/s** single-core with random or fixed actions; construction ~2–4 ms; `reset()` ~0.10–0.37 s [measured].
- Reference: the NLE paper reports **14.4k steps/s** (score task) on a MacBook Pro (Intel i7 2.9 GHz, 16 GB), vs 0.9k for ALE Montezuma and 0.06k for MineRL — NetHack simulation is exceptionally cheap ([Küttler et al. 2020, Appendix D Table 4](https://arxiv.org/abs/2006.13760)). Our M4 Max is ~1.5× that MacBook on this workload [derived].

So < 10% of AutoAscend's per-step cost is NetHack itself; the rest is the bot's Python/numba logic plus the sandbox IPC.

### 1.2 AutoAscend through the real arena path

Measured through the full production path (spawned bot subprocess, per-step observation over a pipe — i.e. *including* the sandbox overhead the project will actually pay):

| Phase | Cost [measured] |
|---|---|
| Cold start (empty `NUMBA_CACHE_DIR`): child spawn + imports + JIT compile | **22.0 s**, once per cache lifetime |
| Warm start: child spawn + imports | **0.69–0.80 s** per episode |
| Agent construction (`client.reset`) | ~0.13 s |
| First action latency (warm) | ~0.2 ms |
| Steady-state stepping, solo | **1,460–1,925 steps/s** (1,825–1,841 on a long solo episode) |
| Steady-state stepping, 12–16 parallel episodes | **1,067–1,326 steps/s** per episode (mean 1,179 in the random batch) |

The numba cache (`bot.py` sets `NUMBA_CACHE_DIR` under the system temp dir) persists across processes and runs, so the 22 s compile is paid once per machine/solution-version, not per episode. With per-episode warm startup ~0.8 s against ~23–34 s of play, process-per-episode overhead is ~2–3% [derived].

**No depth-dependent slowdown observed:** correlation of per-episode steps/s with reached depth is +0.06 (p=0.54, n=96, Dlvl 1–11) [measured]. Cost per episode scales with *episode length*, not depth per se. (Deeper game phases than Dlvl 11 are unobserved — see §8.)

### 1.3 Episode length and wall-clock

| Batch [measured, n=32 each] | steps mean / median | turns mean | wall mean / median / max |
|---|---|---|---|
| random `@` (challenge-style draw) | 25,957 / 25,472 | 18,084 | **23.2 s / 22.4 s / 44.5 s** |
| `val-dwa-law-fem` (strongest identity) | 42,966 / 42,730 | 26,460 | **34.3 s / 36.9 s / 76.2 s** (max episode 106,872 steps) |

External validation of episode length: NLD-AA (109,545 AutoAscend games) has **median 28,181 transitions / 20,414 turns per episode** ([Hambro et al. 2022, "Dungeons and Data"](https://arxiv.org/abs/2211.00539)); "NetHack is Hard to Hack" measures mean survival **19,586 turns** over 3,402 seeded games ([Piterbarg et al. 2023](https://arxiv.org/abs/2305.19240)). Our random batch (mean 25,957 steps / 18,084 turns, steps/turns ≈ 1.57) matches both [measured vs cited].

**Cost grows with bot quality.** Valkyrie episodes already cost 1.5× the random draw because the bot survives longer. Human ascensions take ~50k–100k+ turns ([NetHack wiki, Speed ascension](https://nethackwiki.com/wiki/Speed_ascension)); at steps/turns ≈ 1.57 an ascension-grade episode is ~80–160k steps ≈ **60–120 s on this machine** [derived]. Budget models should assume per-episode cost rises 2–4× as bots improve. The arena caps (1M steps, 10k no-progress) were never approached in 96 episodes (max 107k steps) [measured].

### 1.4 Machine throughput and footprint

- **Measured sustained throughput: 0.30 episodes/s ≈ 1,080 episodes/hour** with 12 concurrent episodes on the M4 Max (two co-loaded batches of 6 workers each: 32/209 s + 16/102 s) [measured]. Each concurrent episode ≈ 350 MB RSS (bot child 295 MB, driver+env 44 MB) → 12 workers ≈ 4.2 GB — fits an 8 GB machine at ~8 workers [measured].
- **Typical-contributor extrapolation:** the NLE-paper MacBook is ~1.5× slower than this machine on raw NLE; assuming the same factor on bot logic, a 4-core 2017-i7-class laptop runs ~800–1,200 steps/s solo → ~40–60 s/episode → at 4 workers ≈ **250–400 episodes/hour** [derived — extrapolation, flagged §8].
- **Cross-check against the Challenge:** submissions ran 512 episodes within a 2 h window on AWS g4dn.xlarge (4 vCPU, 8 GB) ([Hambro et al. 2022, "Insights"](https://arxiv.org/abs/2203.11889)) → 4.27 episodes/min → ~56 s/episode at 4-way parallelism [derived; parallelism inferred, §8]. Consistent with the laptop estimate.
- **Reference cost of a famous artifact:** generating NLD-AA's 3.48B transitions at our measured ~1,300 steps/s ≈ **745 core-hours ≈ 31 core-days** on M4-class cores (2–4× more on the hardware of its day) [derived — generation compute is not published, §8].

---

## 2. How noisy is one episode? (fixed bot, seeded env)

### 2.1 Determinism: the only randomness is the seed

- Same `TrajectorySpec` → **bit-identical outcome**, verified twice (repeat of a probe episode; an independent re-run of a batch episode reproduced steps=90,243, score=52,629 exactly) [measured].
- Flipping `bot_seed` (the sandbox seeds `random` and NumPy in the child) changed **nothing: 16/16 episodes identical** — AutoAscend's decisions don't consume those RNGs. The env seed is the *entire* source of outcome variance for this bot [measured].

Consequences: (i) tier-2 replay verification is sound and cheap; (ii) all "noise" below is *across-seed* generalization noise, not run-to-run jitter; (iii) a fixed public seed set is a fixed exam — overfitting pressure is structural (the mutation engine sees per-seed results), which is what the held-out tier exists for.

### 2.2 Measured outcome distributions (this report)

**Fixed identity, `val-dwa-law-fem`, n=32 seeds** [measured]:

- BALROG-style progression (project `progress.py`): **mean 0.116, sd 0.067, CV 0.58**; range 0.024–0.255.
- Depth: median 4, but **10/32 episodes die on Dlvl 1** and 5/32 reach Dlvl 8–11. Score: median 16,005, max 54,451.
- The Challenge's observation reproduces: "1 in 20 of the winning agent's Valkyries would get a score greater than 30,000, descend to dungeon level 10" ([Insights](https://arxiv.org/abs/2203.11889)) — here 4/32 episodes ≥ 30k score, 4/32 ≥ Dlvl 9.

**Random `@` (challenge draw), n=32 seeds** [measured]:

- Progression: **mean 0.066, sd 0.031, CV 0.47**. Depth mean 2.47; 14/32 die on Dlvl 1.
- Score: median **5,375** — matching the Challenge's reported AutoAscend median **5,300** and NLD-AA's median **5,422** [cited] — and mean 5,915 vs the true mean **8,556** from 3,402 games [cited] — a 31% error at n=32, a live demonstration of the heavy right tail of score.

Note the *fixed* identity is not less noisy than the random draw (CV 0.58 vs 0.47): a strong role stretches the outcome range (early death → Dlvl 11), it doesn't compress it. Fixing identity buys comparability, not variance reduction.

### 2.3 Published distribution anchors

- **Score is heavy-tailed and role-tied.** "The performance of all agents was significantly tied to their starting role and that such performance was often very heavy-tailed" ([Insights](https://arxiv.org/abs/2203.11889)). AutoAscend over 3,402 seeded games: mean 8,556 ± 187, median 4,918 (mean ≈ 1.74× median); mean Dlvl 3.10 ± 0.04; mean turns 19,586 ± 171; descent behavior explicitly bimodal (camp Dlvl 1 vs dive to ~Dlvl 11) ([Hard to Hack](https://arxiv.org/abs/2305.19240)). If the ± is a standard error, σ_score ≈ 10.9k (CV 1.27); if a 95% CI half-width, σ ≈ 5.6k (CV 0.65) — the paper doesn't say which [cited/derived; flagged §8]. Either way **score CV ≥ 0.65 ≫ progression CV ≈ 0.5**, and the median-vs-mean gap plus our 31%-off sample mean show score means converge slowly.
- **Per-identity difficulty spread of the same bot is large.** Katakomba (NLD-AA repacked into 38 role–race–alignment datasets, ~680 episodes each) spans per-identity median scores from **1,980.5 (hea-gno-neu) to 12,574 (bar-hum-cha)** — a 6.4× spread for one fixed policy ([Kurenkov et al. 2023](https://arxiv.org/abs/2306.08772); [repo](https://github.com/corl-team/katakomba)). Identity rows really are different tasks.
- **Progression-metric noise, independent estimate.** BALROG (whose metric `progress.py` adapts, via `nle-progress`) reports o1-preview at **1.57 ± 0.40 (%)** on NetHack; at its default 5 episodes/env ([config](https://github.com/balrog-ai/BALROG)) that implies sd ≈ 0.9, **CV ≈ 0.57** — the same ballpark as our measured 0.47–0.58, for a completely different agent class ([BALROG](https://arxiv.org/abs/2411.13543); [docs](https://balrog-ai.github.io/docs/envs/nle.html)).
- **Ascension is a rare event even for the best humans, and unobserved for bots on 3.6.x.** All-NAO human games: **0.394%** ascend (33,978 / ~8.6M) ([NAO top deaths](https://alt.org/nethack/topdeaths.html)); NLD-NAO: ~22k of 1.51M ≈ **1.5%** ([Dungeons and Data](https://arxiv.org/abs/2211.00539)); *expert* humans: **15.9%** overall, 47.3% on streak-continuing games (35,131 games, 5,574 ascensions, [codehappy expert data](https://codehappy.net/nethack/data.htm)). Bots: **zero ascensions** in the Challenge's evaluations; the only full-auto bot ascension ever (BotHack, 2015) was on 3.4.3 ([NetHack wiki, Bot](https://nethackwiki.com/wiki/Bot)). The plausible near-term target range for an improved bot is therefore p ∈ [0.1%, 5%], entering from 0.

### 2.4 Same-seed pairing (CRN) mostly does not work — measured

The hope: deterministic bots + shared seeds ⇒ compare candidate vs parent *pairwise per seed* and cancel seed difficulty, slashing N. The test: a minimal mutation (replace ~1/2000 of ordinary-map actions with a harmless SEARCH; menu/prompt continuations exempt) on the same 16 Valkyrie seeds [measured]:

- **All 16 episodes diverged** from base (the perturbation fires ~10–30 times/episode).
- Outcome correlation across the pair, same seed: progression **ρ = −0.36** (p=0.17), score −0.27, depth −0.26 — statistically zero, if anything negative. sd of the paired difference (0.085) ≈ √2 × single-episode sd (0.103): **pairing removes essentially none of the variance** once trajectories diverge. NetHack episode outcomes are chaotic in the perturbation.
- The flip side of determinism still pays: a mutation that doesn't change behavior on a seed produces an *exactly* zero diff (bit-identical trajectory) — divergence itself is detectable per-seed at zero statistical cost. So paired evaluation cleanly answers "*does* this change behavior, and where?", but on diverged seeds the outcomes are effectively fresh independent draws.
- Sobering side-finding: this near-trivial perturbation **halved mean progression** (0.106 → 0.055, paired 95% CI of the drop [−0.096, −0.005]) [measured]. AutoAscend sits on a fragile behavioral ridge: most behavior-changing edits will be *strongly* harmful. For search this is good news (big negative effects are cheap to detect) and bad news (neutral-to-positive edits are rare).

Caveat: one perturbation type, n=16 pairs; a structured code mutation touching a rare branch would diverge on fewer seeds (those seeds score exact-zero diff), but *on the diverged seeds* there is no reason to expect more correlation than measured here [flagged §8].

---

## 3. How many episodes buy which claim?

Formulas: N for relative SE r of a mean: (CV/r)²; two-arm mean test (α=.05, power .80): N/arm = 2(σ/Δ)²(1.96+0.84)²; witnessing a rate p with 95% prob: ln(.05)/ln(1−p); rule of three: 0 successes in N ⇒ p < 3/N (95%); two proportions: standard normal-approx formula. Anchored to measured sd's (§2.2); Wilson/bootstrap computed numerically.

### 3.1 Mean progression, one identity (sd = 0.067 at mean 0.116)

| Relative SE of the mean | N episodes |
|---|---|
| ±50% (order of magnitude) | ~2 |
| ±25% | **~6** |
| ±10% | **~33** |
| ±5% | ~133 |

Bootstrap of the measured 32 Valkyrie episodes — the spread of a *mean of N*: N=4 → 90% CI ±44% of the mean; **N=8 → ±33%**; N=16 → ±23% [measured/derived]. The project's default `public-8` seed set therefore reports a per-identity mean known to only ±⅓ of itself.

### 3.2 Detecting an improvement over the parent (same identity, unpaired — §2.4 says pairing ≈ unpaired once diverged)

| True improvement Δ (relative to mean 0.116) | N per arm |
|---|---|
| +0.005 (+4%) | ~2,800 |
| +0.01 (+9%) | **~700** |
| +0.02 (+17%) | ~175 |
| +0.03 (+26%) | ~78 |
| +0.05 (+43%) | **~28** |

Inverted — the minimum detectable effect at fixed N (α=.05, power .80):

| N per arm | detectable Δ | as % of mean |
|---|---|---|
| 5 | 0.119 | 102% |
| **8** | **0.094** | **81%** |
| 16 | 0.066 | 57% |
| 32 | 0.047 | 40% |
| 128 | 0.023 | 20% |
| 512 | 0.012 | 10% |
| 4,096 | 0.004 | 3.6% |

A handful of seeds distinguishes only **near-doublings**. That is not useless — §2.4 shows typical behavior-changing mutations have huge (negative) effects, so small N is an effective *cull filter* — but incremental progress (+5–15%) is invisible below N ≈ 100–700.

### 3.3 Ascension (rare events), per objective

| Claim | Episodes |
|---|---|
| Witness a p=5% rate at all (95% prob of ≥1) | ~58 |
| Witness p=1% | **~298** |
| Witness p=0.1% | ~3,000 |
| "0 ascensions in N" caps p at (95%): N=100 → <3%; **N=300 → <1%**; N=1,000 → <0.3%; N=4,096 → <0.073% | rule of three |
| Distinguish 1% vs 5% | ~284/arm |
| Distinguish 2% vs 5% | ~588/arm |
| Distinguish 1% vs 2% | **~2,318/arm** |
| Estimate 1% to ±25% rel. | ~1,600 |
| Estimate 1% to ±10% rel. | **~9,900** |

Once ascensions exist, *rate* comparisons between solutions are 10²–10⁴-episode questions. Until then, ascension is a **witness event** (one verified episode proves capability — N-free) plus a **floor certificate** ("0 in N").

### 3.4 Why the Challenge's magnitudes were what they were

The Challenge ranked by (1) ascensions, (2) median score, (3) mean score, with random characters, episodes killed if score <1,000 by 50k steps, ≤30 min/episode, ≤300 s/action ([AIcrowd rules](https://www.aicrowd.com/challenges/neurips-2021-the-nethack-challenge/challenge_rules)); dev = 512 episodes / 2 h, final = 4,096 / 24 h on g4dn.xlarge ([Insights](https://arxiv.org/abs/2203.11889)).

- **512 (dev)** ≈ what 4 vCPUs finish in 2 h at ~56 s/episode [derived] — a *hardware* budget first. Statistically it gives median-score SE ≈ ±6% (lognormal approximation from the mean/median ratio 8,556/4,918 [cited §2.3]) and a 0.6% ascension floor (3/512) — enough for a rolling leaderboard [derived].
- **4,096 (final)** is what one instance finishes in a day [derived]. Statistically it (a) pins the median to ~±2% (same lognormal approx); (b) bounds an unobserved ascension rate below **0.073%** — i.e. certifies "no ascensions" against even sub-NAO-human rates; (c) tames the heavy-tailed mean to ±1–2% rel. SE (CV 0.65–1.27 [§2.3]) [derived]. To make a *comparable* per-cell claim (one identity, mean progression instead of median score), §3.1's table says **N ≈ 33 per identity for ±10%** — comparable discriminating power at ~1/100th the episodes, because progression has CV ≈ 0.5 vs score's ≥0.65 *and* means of bounded metrics don't chase a heavy tail.

---

## 4. Contributor budget model

Throughput [measured/derived, §1.4]: M4-Max-class ≈ **1,100 eps/h** (12 workers); mid-range 4-core laptop ≈ **300–400 eps/h**; per-episode cost rises toward 60–120 s as bots deepen (§1.3).

Wall-clock for the standard evaluation shapes (at today's ~27 s mean episode):

| Evaluation | Episodes | M4 Max (12w) | 4-core laptop (4w, 250–400 eps/h) |
|---|---|---|---|
| `public-8`, one identity | 8 | ~20 s | ~1.5–2 min |
| N=32, one identity | 32 | ~1.2 min | ~5–8 min |
| N=128, one identity | 128 | ~5 min | ~20–30 min |
| `random`, fixed batch of 400 | 400 | ~15–25 min | ~1–1.6 h |
| All 73 identities × 8 | 584 | ~22 min | ~1.5–2.3 h |
| All 73 × 32 | 2,336 | **~1.5–2.5 h** | **~6–9 h** |
| Challenge-final-scale 4,096 | 4,096 | ~2.6–4 h | ~10–16 h |

(Laptop column at today's episode lengths; both columns stretch 2–4× as bots deepen, §1.3.)

Per-day capacity (24 h flat-out): **~26k episodes (M4 Max) / ~7–9k (laptop)** [derived]. In candidates: at N=8/candidate the eval side supports ~3,000 (M4) / ~900 (laptop) candidates/day — far more than an LLM mutation engine will produce (a headless coding-agent mutation takes minutes → ~100–500 candidates/day). **On strong hardware, evaluation is not the bottleneck of the inner loop below N ≈ 32–64 per candidate; the mutation engine is.** On weak laptops the crossover sits nearer N ≈ 8–16.

---

## 5. Checking the discussion's moves against the numbers

**Move 1 — "search cheaply on a narrow objective (a handful of seeds per candidate)."**
**Justified as a cull, not as a ranking — and on fast machines it undersells the affordable N.** A handful (5–8) episodes costs seconds-to-minutes (§4) and reliably catches the dominant failure mode: §2.4 shows a near-trivial behavioral change cut mean progression in half, an effect size N=8 *can* see (Δ ≈ 0.05–0.09 detectable, §3.2). But N=8 cannot see genuine incremental wins (+9% needs ~700/arm) and its mean is only known to ±33% (§3.1) — so any *selection* among surviving candidates at N=8 is mostly picking noise, and archives filled by max-of-noisy-small-N estimates inflate systematically (winner's curse), which fixed deterministic seeds convert into overfitting pressure on the public set. The numbers support: handful-N as the reject-gate, escalation (8 → 32 → 128, ~1–5 extra minutes on M4) before a candidate is *promoted or registered*, and treating exact-zero-diff seeds (determinism, §2.4) as free behavior-change detection. One free lunch the current default forgoes: at ~30 s/episode, `public-8` → `public-32` costs ~1 extra minute on an M4 (~5 on a laptop) and tightens the reported mean from ±33% to ±17% (90% CI, §3.1).

**Move 2 — "`random` = a bounded fixed batch of a few hundred games, run only occasionally."**
**Justified, and statistically stronger than 'occasionally' implies.** A fixed 400-episode batch costs ~20 min (M4) / an evening (laptop) (§4). At the random-draw CV of 0.47 [measured] the mean progression carries a **±2.4% relative SE** — a genuinely precise headline number — and doubles as an ascension floor: 0-in-400 ⇒ p < 0.75% (§3.3). Two provisos the numbers force: (a) a few hundred episodes say *nothing* about ascension-rate differences at the ≤1% level (that's a 2,000–10,000-episode question, §3.3), so the batch certifies progression, not the north star; (b) comparability requires all solutions to run the *same* fixed batch (it freezes the identity mixture — role explains a large outcome share [§2.3]); scores from different random draws of a few hundred are not comparable at the precision the batch otherwise buys.

**Move 3 — "per-solution `all` (73 × N episodes) is infeasible → cut it as a board."**
**Justified as a per-candidate/inner-loop object; overstated as an absolute.** 73×N at a *meaningful* per-cell N (≥33 for ±10%, §3.1) is ~2,400 episodes ≈ 1.5–2.5 h on an M4 Max, ~6–9 h on a laptop at today's episode lengths — stretching toward a full day as bots deepen (§4) — clearly not an inner-loop or per-registration requirement, and at an *affordable* small N (73×8 ≈ 22 min M4) the per-cell means are ±33% noise, i.e. the board would rank noise. So as a routinely-computed per-solution leaderboard it is rightly cut. But the same table shows a *rationed* full-catalog run on a flagship solution is a ~2-hour verifier-side job — feasible as an occasional tier-3 certification, which is exactly the role the design assigns to held-out verification rather than to a board.

**Move 4 — "the 73-row map fills collectively from many cheap narrow runs, not one bot running all 73."**
**Justified by both cost and structure.** Cost: one identity at credible N (32) is a ~1–15 min unit of work (§4) — the natural contributor quantum; 73 rows × 32 from one contributor is most of a laptop-day (§4) for numbers a single bot would spread thin. Structure: identities genuinely differ for a fixed policy (6.4× spread in per-identity median score, Katakomba [§2.3]; "performance … significantly tied to their starting role", Insights), so per-row optimization pressure is real, and rows are where the variance lives. Two number-driven conditions for the collective fill to mean anything: (a) per-row evidence must be on the row's canonical seed set and reach N ≈ 30+ before cell *rankings* are trusted (below that, only witness-style claims — "reached rung R on seed s" — are solid, and those are N-free by determinism); (b) `random`-view estimates composed from per-identity rows inherit each row's SE — a map rollup at rows of N=8 has cell noise of ±33%, so the rollup should surface N per cell, or the map silently launders small-N noise into leaderboard positions.

**Cross-cutting fact the moves rely on implicitly — confirmed:** the fitness metric choice is what makes any of this affordable. Progression (CV ≈ 0.5, bounded) reaches ±10% per-identity claims at N≈33; raw score (CV 0.65–1.27, heavy-tailed) would need N in the hundreds-to-thousands for the same relative precision, which is exactly why the Challenge needed 4,096 episodes for stable score statements (§3.4).

---

## 6. Feasible-regime summary (the cost+variance model's output)

| Regime | Episodes | Feasible where | What it can honestly claim |
|---|---|---|---|
| Inner-loop cull | 5–8 per candidate | any laptop, seconds–minutes | reject catastrophic regressions (effect magnitude ≳ 0.09); detect behavior change exactly (zero-diff seeds); witness single-seed rung "firsts" |
| Promotion gate | 32 per candidate (escalate to 128 on survivors) | any laptop, minutes | per-identity mean to ±10% SE; detects Δ ≥ 0.047 (+40%) vs parent, and Δ ≥ 0.023 (+20%) after escalating to 128 |
| Registered per-identity evidence | ≥33 per identity on the canonical seed set | laptop, ~15 min/identity | a map-cell mean worth ranking |
| `random` headline batch | 256–400 fixed seeds | M4 ~20 min; laptop overnight | mean progression ±2.4–3%; ascension floor <0.75–1.2% |
| Full catalog 73×32 | ~2,300 | strong machine / hub verifier, hours | tier-3-style certification of a flagship; not an inner loop |
| Ascension-rate science | 300 (witness 1%) → 2,300+ (1% vs 2%) → ~10k (1% ±10%) | hub-scale / community-aggregate only | the north-star metric, once it's nonzero |

---

## 7. Sources

**Measured artifacts (this report):** [`data/2026-08-09-eval-cost/`](data/2026-08-09-eval-cost/) — `bench_nle_raw.py`, `bench_autoascend.py`, `stats_model.py`, `perturbed_bot.py` (the §2.4 minimal mutation), `ep_random.jsonl` (32 eps), `ep_valkyrie.jsonl` (32), `ep_valkyrie_botseed1.jsonl` (16), `ep_valkyrie_perturbed.jsonl` (16) — evaluated against this repo @ `9433e52` with `nle==1.3.0` + the `autoascend` extra installed. (Note: `bench_autoascend.py` and `perturbed_bot.py` contain absolute paths from the measurement session; adjust before re-running.)

**Published:**

- NLE: Küttler et al., *The NetHack Learning Environment*, NeurIPS 2020 — https://arxiv.org/abs/2006.13760 (speed: Appendix D, Table 4); repo https://github.com/facebookresearch/nle
- Challenge: Hambro et al., *Insights From the NeurIPS 2021 NetHack Challenge* — https://arxiv.org/abs/2203.11889; rules https://www.aicrowd.com/challenges/neurips-2021-the-nethack-challenge/challenge_rules
- Datasets: Hambro et al., *Dungeons and Data: A Large-Scale NetHack Dataset* (NLD-AA / NLD-NAO) — https://arxiv.org/abs/2211.00539
- AutoAscend internals & 3,402-game evaluation: Piterbarg, Pinto, Fergus, *NetHack is Hard to Hack*, NeurIPS 2023 — https://arxiv.org/abs/2305.19240; bot repo https://github.com/maciej-sypetkowski/autoascend
- Per-identity spread: Kurenkov et al., *Katakomba: Tools and Benchmarks for Data-Driven NetHack*, NeurIPS 2023 D&B — https://arxiv.org/abs/2306.08772; https://github.com/corl-team/katakomba
- Progression metric: Paglieri et al., *BALROG* — https://arxiv.org/abs/2411.13543; docs https://balrog-ai.github.io/docs/envs/nle.html; repo (eval config: `num_episodes: nle: 5`, `max_episode_steps: 100_000`) https://github.com/balrog-ai/BALROG. (`progress.py` header: values adapted from `nle-progress`, which adapted BALROG; no public `nle-progress` package was found — see §8.)
- Human baselines: NAO top-deaths (0.394% ascension) https://alt.org/nethack/topdeaths.html; codehappy expert dataset https://codehappy.net/nethack/data.htm; NetHack wiki (Bot; Speed ascension) https://nethackwiki.com/wiki/Bot, https://nethackwiki.com/wiki/Speed_ascension

## 8. Missing data / caveats (flagged, not guessed)

1. **Pairing correlation for real LLM mutations.** §2.4 uses one synthetic perturbation, n=16 pairs (ρ CI ≈ ±0.5). Real code mutations will diverge on fewer seeds (rare branches) — the *diverged-seed* correlation is the open quantity; nothing measured here supports assuming it exceeds ~0.
2. **sd estimates rest on n=32 per arm** — a χ² 95% CI puts the true sd within ×[0.80, 1.27] of the estimates; all N tables inherit that factor (squared: ×[0.64, 1.6]).
3. **Deep-game cost (Dlvl > 11, Quest/Gehennom/Planes) is unobserved** — no current bot reaches it; per-step cost there (bigger levels, heavier inventories, more menu traffic) and true ascension-episode length are extrapolations (§1.3).
4. **"Hard to Hack" ± ambiguity** (SEM vs CI) leaves score σ ∈ [5.6k, 10.9k]; conclusions only use the lower bound.
5. **Challenge parallelism** (episodes concurrently per g4dn.xlarge) is inferred from vCPU count, not documented; per-episode wall on their hardware is accurate only to that assumption. The Challenge's total games played across all submissions (>500k is often quoted) is likewise not stated in the paper — only 512/submission (dev) and 4,096 (final×3) are.
6. **BALROG's NetHack episode count** in the paper's headline table is not stated per-env in the text; the repo's default config (5) was used to derive its implied CV.
7. **NLD-AA generation compute** is not published; §1.4's 745 core-hours is our SPS-based reconstruction.
8. **`nle-progress`** exists only as the provenance note in `progress.py` (sibling project); no public package located to diff the calibration table against BALROG's Appendix F.2 values.
9. **Old-laptop throughput** is a 1.5×-scaling extrapolation anchored on one published MacBook datapoint, not a measurement on such hardware.
10. **Failure rates:** 0 bot errors/timeouts in 96 sandboxed episodes here; the Challenge-era AutoAscend README warns of exception-prone edge states. Failure-rate under mutated variants (the thing the crash-penalty term prices) is unmeasured.
