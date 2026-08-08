# NetHackers — Design Spec

- **Date:** 2026-08-08
- **Status:** Draft for review
- **Supersedes:** the v1 prototype (`dunnolab/nethackers`, `dunnolab/nethackers-internal`)
- **Supporting research:** [`../research/2026-08-08-map-elites-objectives-analysis.md`](../research/2026-08-08-map-elites-objectives-analysis.md), [`../research/2026-08-08-autoascend-challenge-interface.md`](../research/2026-08-08-autoascend-challenge-interface.md), [`../research/2026-08-08-nethack-community-insights.md`](../research/2026-08-08-nethack-community-insights.md)

---

## 1. Context & motivation

NetHackers is both an **infrastructure** project and a **challenge**: a lean, distributed, viral effort to *solve NetHack* (reliably ascend) by having LLM-driven harnesses evolve **deterministic symbolic** NetHack player programs, building on **AutoAscend** (the strongest known bot, winner of the NeurIPS 2021 NetHack Challenge).

A v1 prototype exists but is being discarded. It was one-shot generated and became bloated: the platform tried to *own* evaluation via a heavy backend — a private hub with canonical trusted replay, a reputation system, two full contours (production + development) with separate databases, Caddy/systemd VMs, integrity-pinned PyPI runtime, and versioned federation schemas. That mass is the thing v1 got wrong. A second, quieter failure: **the full pipeline could not be exercised locally** — the server-side pieces lived only on deployed infra.

v2 starts from scratch with two governing lessons: **stay lean** (push work to the edge; the hub is a thin registry), and **everything runs locally** (container-parity from day one).

A sibling project, `dunnolab/nethack-autoascend-challenge`, explores a "classic harness challenge" in parallel. The two intend to **converge on shared decisions — above all, the agent interface.** This spec adopts that project's `ArenaBot` interface and several of its components (see §4, §15).

## 2. Goals & non-goals

**Goals**
- A tiny, universal core: comfortable **local evaluation/verification** and **registration** of solutions.
- A **thin, queryable hub**: a registry of GitHub-hosted solutions + metadata, with derived views (a global archive, many leaderboards, lineage/impact).
- A default **distributed evolution** on-ramp that newbies can just *run in the background*, plus complete freedom for advanced users to search however they like.
- **Viral** surfaces: public repos, a community leaderboard, per-contributor "impact", and per-run shareable pages.
- **Detailed progress tracking** toward solving NetHack, including **per-character** optimization (not just the random draw).
- **Container-parity**: the entire system (evaluator, verifier, hub) runs on a laptop via `docker compose up`, identical to production.

**Non-goals**
- The platform does **not** own or run contributors' search/harness. It provides evaluation + registration + a queryable hub; how solutions are produced is unconstrained.
- No reputation system, no dual contours, no integrity-pinned runtime distribution (all v1 mass).
- No stochastic/LLM-in-the-loop *players* in the core model (see §15). The LLM is the mutation engine, not the player.

## 3. Core principles

1. **Push work to the edge; keep the hub thin.** Contributors own compute, model access, and evaluation. The hub stores references + metadata + derived indexes. It never executes candidate code during a request.
2. **Evidence is a ladder, not a gate.** Every result carries a tier; nothing blocks on the platform. Verification is additive.
3. **Container-parity / local-first.** One way to run anything — a container. `docker compose up` = the whole system on localhost, identical to prod. The pinned evaluator image *is* the environment contract.
4. **Determinism is the foundation.** Symbolic players + seeded NLE make tier-2 replay cheap, tier-3 reproducible, and scores comparable. The LLM lives in the mutation engine, never in the player.
5. **Milestones add capability, not parallel mechanisms.** One artifact format, one environment, one evaluation.
6. **Every rendered surface is previewable locally from fixtures.** Anything a person looks at (the repo card, leaderboards, the map, per-run/profile pages, CLI output) renders on the developer's machine from saved sample data with live reload — no real run and no publishing required.

## 4. Domain model & contracts

The platform imposes exactly **one** coupling on contributors: a *solution* must implement the `ArenaBot` interface, and its evidence must be in our format. Everything else (how solutions are produced) is free.

**`ArenaBot` interface** (adopted from the sibling challenge repo, essentially verbatim):

```python
def make_agent() -> ArenaBot: ...

class ArenaBot(Protocol):
    def reset(self, initial_observation: Mapping[str, Any]) -> None: ...
    def act(self, observation: Mapping[str, Any]) -> int:  # index into nle.nethack.ACTIONS
        ...
    def close(self) -> None:  # optional (ClosableArenaBot)
        ...
```

- **Observation**: the read-only raw NLE dict (14 fixed keys: `glyphs, chars, colors, specials, blstats, message, inv_glyphs, inv_strs, inv_letters, inv_oclasses, tty_chars, tty_colors, tty_cursor, misc`). `act()` receives only the observation — no reward/done/info. Termination is the evaluator's business.
- **Action**: a single `int` index into `nle.nethack.ACTIONS` (one NLE action per call; `bool` rejected).
- **Lifecycle**: `make_agent()` → `reset(obs0)` → `act()*` → optional `close()`. One instance per episode, one env per bot, no batching.
- **Key property**: a program is **character-agnostic in interface** — a symbolic bot reads its own stats/inventory and adapts, so *one program plays every character*. The objective never touches the interface; it only tells the evaluator which characters/seeds to run. This is why the same solution can occupy many identity rows and why cross-character transfer works.

**Solution** — a full-program repo implementing `ArenaBot`, plus a manifest declaring:
- `root` (default `autoascend`), `parents` (0–2 solution digests), `influences` (weak-credit refs), and a content `digest` (sha256 over canonical manifest + source).
The hub stores the **reference** (repo URL + commit + manifest), never the bytes. Bytes are fetched from GitHub on `pull`.

**Objective** — what you optimize: a character distribution + fixed horizon + a public seed set (+ a private held-out seed set on the server). Examples: `random` (natural draw), `all` (uniform over roles), slices (`role:valkyrie`, `race:elf`, `alignment:lawful`), a specific identity (`val-dwa-law-fem`), and (deferred, §13) community-native variants (conducts, turncount, streaks). Every objective has a canonical digest; scores are never ranked across objective digests.

**Evidence** — `(objective, seed matrix, per-episode results, mean progression, ascension count, evaluator-image digest, tier, timestamps, failure taxonomy)`. `tier ∈ {self-reported, replay-verified, heldout-verified}` — same schema across tiers; the tier is a field. Records are **xlogfile-compatible** (the community's interchange format).

**Lineage** — the parent/influence DAG over solutions; the basis for impact (§10).

## 5. Evaluation: environment & the three-tier ladder

**The pinned evaluator image is the contract.** One Docker image whose digest is recorded in every Evidence record. It pins Python 3.11, `nle==1.3.0` (NetHack 3.6.6), the fitness code (`progress.py`), single-threaded BLAS, `PYTHONHASHSEED=0`. Because all tiers use the same image, scores are comparable by construction.

**Sandbox & budgets** (adopted from the sibling repo): the agent runs in a `spawn` subprocess over a pipe; the seed secret is scrubbed from its env; per-`act` timeout ~5 s; per-episode `max_steps` = 1 M (the NLE Challenge horizon), no-progress 10 k, plus a trajectory timeout. Failure taxonomy: `bot_error / bot_timeout / invalid_action / trajectory_timeout / infrastructure_error`. An `infrastructure_error` invalidates a run rather than penalizing the bot. The local loop may use a shorter horizon for fast iteration, but horizon is part of the suite identity — scores across horizons don't mix.

**The ladder — one metric (mean progression), only the seed set changes:**

| Tier | Who runs it | Seeds | Cost | Meaning |
|---|---|---|---|---|
| 1 · self-reported | contributor, locally | **public** set | instant | drives the loop; unverified |
| 2 · replay-verified | hub verifier | same **public** seeds | cheap — one deterministic replay of the *submitted actions*, no program re-run | catches lies/bugs |
| 3 · heldout-verified | hub | **secret held-out** set (HMAC-derived) | expensive, rationed | anti-overfitting gold standard |

**Held-out seeds** are HMAC-SHA256-derived from `secret ‖ evaluation_id ‖ trajectory_id` (adopted from the sibling repo). New eval-id or secret ⇒ all seeds change; reuse both ⇒ exact reproducible resume. Only the secret's fingerprint is persisted; the secret is a deployment env var.

**Trust mechanism (nearly free):** if tier-2 replay diverges from the claim, the evidence is flagged — that divergence is *either* a bug *or* dishonesty, and we don't need to care which. No reputation system.

## 6. Fitness metric & the progress ladder

Two different needs, two different metrics:

- **Search signal (fitness)** — must be dense, monotonic, low-variance, aligned with winning.
- **Win condition (north star)** — ascension rate; the true goal, but ≈0 for essentially the whole search.

**Primary fitness = a calibrated progression metric** (BALROG / `nle-progress` style — i.e. the sibling repo's `progress.py`), ∈ [0,1], built from milestones (dungeon depth, experience level, reached-Quest, reached-Astral, ascension) calibrated to empirical ascension probability. Raw NetHack score is rejected as fitness (farm-able, high-variance, misaligned — the classic NetHack-Challenge mistake) and kept only as a diagnostic. Ascension-rate-as-fitness is rejected (flat at zero, no gradient) and kept as the separate north star.

**Aggregation:** mean over the objective's seed set (not median — a rare deep run or early ascension is exactly the tail we want to reward). Track ascension count/rate as an explicit secondary. Rank by mean-progression → ascensions → median. Penalize crashes/timeouts/invalid actions (a crash-rate term); validity is both a fitness penalty and a local-archive descriptor.

**The progress ladder** (the map's columns) is the *same object* as the fitness milestones, anchored to the **community-validated** devnull "star ladder" ∪ TNNT "Lesser"-trophy milestones: Sokoban → Mines' End → nemesis → Medusa → Castle → Gehennom → Vlad → Rodney → invocation → Amulet → Planes → Astral → **Ascension**. Choosing the metric locks the columns.

## 7. Objectives, the archive & selection

The question "should each objective have its own archive?" decomposes into three independent scopes (see the MAP-Elites research report):

1. **Competition scope — per objective.** A Valkyrie score and a Wizard score measure different games; they must never rank against each other. → **per-objective evidence pools.**
2. **Selection scope — global.** Every solution is a program over the same AutoAscend root, so a good idea for any character is one re-evaluation away from helping another. Parents are drawn across *all* objectives; imports are **re-scored on the active objective before they count** (safe transfer). This is multi-task MAP-Elites (per-task competition, cross-task selection). → **one global gene pool.**
3. **Presentation scope — one map.** The canonical public archive is **one identity×progress map**: rows = the ~73 valid character identities, columns = the progress ladder (§6). Each cell = the best-known solution getting that identity to that depth. `random`, `all`, slices are **views/rollups** over this one map. Grid coverage = how close the community is to solving NetHack.

This is the research's **Option D** (reject per-objective archives — the objective space is combinatorially open, ~2⁷³; reject objective-as-a-dimension — it compares incomparable scores). The distributed community is *already* an island model (each contributor run = an island, the hub = the migration medium, import-and-re-evaluate = migration), so the hub needs **zero** island state.

**Two archive-like structures at two layers:**
- **Local search archive** (inside a contributor's run): MAP-Elites keyed by *behavioral descriptors* — a lean default of `max progress-rung × a behavior axis (e.g. death-cause category) × crash/validity`, weighted to the early game. The search engine; on the contributor's machine. Descriptors are a tunable knob, not a contract.
- **Hub canonical map** (global, derived, public): the identity×progress map. The coordination + progress layer.

Default parent-selection heuristic: pair a weakest-rung specialist with a generalist elite.

## 8. Platform primitives + CLI

The platform is a small CLI + library — symmetric read/write over the contracts:

- **Local:** `nethackers eval <solution>` — run + verify a solution in the pinned container on a seed set → an Evidence record (tier-1). Container-parity means this is the *exact* evaluation the server runs, so contributors can verify their own solution and reproduce anyone else's. Same code powers server tiers 2/3.
- **Read (hub):** `search` (by objective, tier, score, progress-rung, lineage, author, conduct…), `show`, `pull <solution>` (fetch repo@commit locally to build on / re-evaluate / use as a parent), `elites --objective X` (the global archive's best-per-cell), `map`, `leaderboard`. Returns references + evidence summaries; bytes come from GitHub via `pull`.
- **Write (hub):** `register` / `publish` — publish the `<owner>/nethacker` repo (with the rendered card) and register the reference + evidence with the hub.

The read API is a single surface with three consumers: the CLI, the website, and the optional evolution tool. This is the founding "CLI for querying existing solutions."

## 9. The optional evolution reference tool

An **optional** starter kit (`nethackers evolve`), not a framework anyone plugs into — a client of `eval` + the hub primitives. It runs a background loop:

1. **Select** parents (local archive; + hub `elites` once the hub exists).
2. **Mutate** — hand parents + context to a pluggable mutator → a candidate solution.
3. **Smoke-test** — cheap validity gate (imports cleanly, plays a few steps without crashing) before spending eval budget.
4. **Evaluate** — `eval` in the pinned container → tier-1 Evidence.
5. **Place** — insert into the local MAP-Elites archive (best-per-cell).
6. **Publish** if a new/improved elite — `register`.

**Mutator** (pluggable): `mutate(parents, context) -> candidate`. `context` = the objective, a version-pinned **3.6.6 strategy knowledge pack** (Elbereth/prayer/BUC/ascension-kit rules correct for our NLE version — older lore is wrong on 3.6.6), the early-game curriculum, and optional hints. Default impl shells out to a **headless coding agent** (`claude -p`, `codex exec`, Agent SDK) in an isolated working copy of a parent — full agentic power, cost-free via the contributor's subscription, unattended because headless. Alt impl = a raw LLM-API call with the contributor's key.

**Curriculum** (from community research): the game is decided early — top killers are Dlvl 1–5, and AutoAscend's mean ending depth is ~Dlvl 3. Progression fitness *naturally* front-loads the early game (that's where the gradient is); the knowledge pack + hints steer the mutator there explicitly. Late-game modules (Quest/Gehennom/Planes) are well-separated targets that matter once Castle-reach is reliable.

Advanced users ignore this tool entirely and call `eval` + `register` from their own search, in any tech.

## 10. The hub

**Stance:** a queryable index of solution references + evidence, plus derived views. Stores references + metadata, never candidate bytes; never executes candidate code during a request (execution = offline tier-2/3 workers).

- **Stores:** solution references (repo + commit + manifest + digest); Evidence records (xlogfile-compatible); recomputed derived indexes (the identity×progress archive, leaderboards, lineage graph, impact).
- **Read/write API:** §8. `register` does a cheap validation (repo/commit exists, manifest well-formed, digest matches — no execution) then indexes as self-reported; offline workers escalate to tier-2 replay and rationed tier-3 held-out; the evidence tier upgrades in place and derived views recompute.
- **Leaderboards — many small boards, never one score** (TNNT abandoned unified scoring as unfixably biased): per-objective boards; map-coverage (Z-score style); "firsts" (first to reach rung R for identity I — named credit); "best YASD / most unique deaths" (diagnostic + on-culture); conduct/turncount/streak boards. Every board filters by evidence tier.
- **Impact:** lineage DAG from manifests — parents weight 1, influences 0.25, per-objective, one split point per child (no recursive credit). A contributor's impact = their solutions' contribution to elites + firsts.
- **Trust posture (lean):** self-reported by default (labeled); cheap tier-2 replay catches lies/bugs; tier-3 for the top of a board. No reputation, no dual contours. Replay divergence → flag.
- **Architecture:** a small API + derived-view store (SQLite) + offline verification workers (pinned evaluator image) + the website (a view over the read API). All containers; `docker compose up` runs the whole hub locally on fixtures.

## 11. Rendered surfaces & local preview

Each surface is produced by a **pure render function** of sample data, driven by shared **fixtures**, previewable locally with live reload (principle #6):

| Surface | What it is | Preview |
|---|---|---|
| `<owner>/nethacker` README card | the repo's public "look" (name, program, objective, score, tier, lineage, progress) | render locally styled like GitHub; edit → auto-refresh; no push |
| Leaderboards | the hub's ranking pages | local hub + fixtures; hot reload |
| Progress map | the 73×rungs grid | local hub + fixtures |
| Per-run page | one solution's shareable permalink result page | one sample run → local page |
| Profile page | a contributor's solutions/impact/firsts | one sample profile → local page |
| CLI output | terminal status + the ASCII banner card | run against sample data |
| Badge / share image (optional) | score/tier badge + link-share card | generate from sample data |

**Fidelity:** for the README, render offline with `cmarkgfm` (GitHub's own GFM parser) + `github-markdown-css` for the fast loop; check fidelity with `grip` / GitHub's `/markdown` API / a throwaway push. Keep the card to GitHub-safe primitives (a fenced-code ASCII stat card, tables, badges) so local ≈ GitHub by construction. One fixture dataset drives *both* the README preview and the hub/web preview, so every surface stays consistent.

## 12. Repo/package structure, distribution, naming & migration

**One monorepo.** The only true secret is the tier-3 held-out HMAC key (a runtime env var, not code), so all code *can* be public — which serves the "public repos" pillar and lets anyone run the whole hub locally. **Private during development; flip to public at launch.** Production secrets + deploy config live in env, never committed.

```
nethackers/                     # dev repo: private dunnolab/nethackers-v1 → public dunnolab/nethackers at launch
  src/nethackers/               # the pip/uv-installed package (thin: CLI + library)
    contracts/                  # ArenaBot interface · Solution manifest · Objective · Evidence
    eval/                       # local evaluator (orchestrates the pinned image) -> Evidence
    hubclient/                  # search · show · pull · elites · register
    render/                     # render card -> markdown/SVG + preview server
    publish/                    # write <owner>/nethacker repo (+ hubclient.register)
    evolution/                  # OPTIONAL reference tool: mutator + local archive + loop
    cli.py
  arena/                        # the pinned evaluator image (published to a registry)
    Dockerfile                  # Python 3.11 · nle==1.3.0 · single-thread BLAS · PYTHONHASHSEED=0
    autoascend/  adapter.py  progress.py  seeds.py   # root · AutoAscend↔ArenaBot bridge · fitness · HMAC seeds
  hub/                          # hub service (M2): api/ (SQLite registry) · workers/ (tier2/3) · web/
  fixtures/                     # shared sample data → drives render preview AND hub/web preview
  knowledge/                    # version-pinned 3.6.6 strategy pack (fed to the mutator)
  regression/                   # YASD seeded death-catalog suite
  compose.yaml                  # `docker compose up` = whole system locally on fixtures
```

**Distribution:** `uv tool install nethackers` (thin CLI) + the arena **image** from a container registry + the hub deployed from the same repo. The AutoAscend root is fetched as a template by `nethackers new`, so the installed package stays small.

**Naming & migration (recommendation — flip at review if desired):** develop in **private `dunnolab/nethackers-v1`** with **`nethackers` as the in-code package name from day one** (repo name ≠ package name; nothing publishes until launch, so no collision with the prototype). At launch: rename `dunnolab/nethackers` → `nethackers-legacy` (or archive it), rename `nethackers-v1` → `nethackers`, and publish v2 as `1.0.0` to the PyPI `nethackers` project you already own (yank prototype releases). **No deletions** — v1 stays as a reference; archive it if it should be out of view.

## 13. Milestones

**M1 — de-risk the local loop** (no hub). Ship: `contracts`; the pinned **arena image** (AutoAscend + adapter + progress + seeds + sandbox); `eval` (local tier-1); `pull <repo@commit>` (direct GitHub); `render` + `fixtures` + `preview`; `publish` to `<owner>/nethacker`; the optional `evolution` reference tool; `knowledge/` + `regression/`.
- **Success:** a contributor's local loop produces a **measurably improved** AutoAscend variant (higher mean progression than baseline on the public seeds), published as a viewable card — and the whole thing runs and tests on a laptop.

**M2 — the hub + the viral loop.** Ship: the hub service (registry + read/write API); offline **tier-2 replay + rationed tier-3 held-out**; the derived identity×progress archive; many-small-boards + lineage/impact; the website; evolution's **global transfer**; `docker compose up` = full system.
- **Success:** join → evolve → register → appear on the map/boards → pull others' elites → compound.

**M3+ (deferred, YAGNI now):** richer descriptors / exploration-vs-robustness island scalarizations; NLD-AA behavioral-drift detection; social/OG share images; a recurring "bot-November" tournament + a death-announcer bot; conduct/turncount/streak objectives surfaced; roots beyond AutoAscend.

## 14. Testing strategy

- **Unit:** contracts/manifest validation, the render function (against fixtures), hubclient, archive-placement, lineage/impact math.
- **End-to-end on localhost** (the thing v1 couldn't do): `docker compose up`, then exercise `eval → register → tier-2 replay → tier-3 held-out → board recompute` entirely locally.
- **Fixture-driven view snapshots** for every rendered surface (card, boards, map, profile) + the `preview` server for eyeballing.
- **Determinism/trust test:** replaying a recorded trace reproduces the claimed score exactly — guards the whole trust model.
- **Golden baseline:** AutoAscend's known progression distribution on the public seeds is a regression gate (baseline parity).
- **YASD regression suite:** fast "did a mutation reintroduce a known death?" check, used by contributors and in CI.
- Implementation discipline: TDD (see the superpowers test-driven-development skill) during the plan.

## 15. Assumptions & open coordination items

- **Deterministic symbolic players (accepted premise).** The LLM is the mutation engine, not the player. This is what makes tier-2 replay reproducible and scores comparable. Revisit only if stochastic/LLM-in-the-loop players are ever wanted — it would break tier-2 reproducibility and require rethinking evidence.
- **Sibling-project convergence (`dunnolab/nethack-autoascend-challenge`).** Adopt its `ArenaBot` interface, `arena_adapter.py` (AutoAscend control-inversion bridge), HMAC held-out seed scheme, and `progress.py` metric. **To confirm with the co-worker:** (1) that `baseline` is the canonical branch (it's only 2 commits, rationale not in commit messages); (2) their explicit opinion on the interface; (3) alignment on the exact progression metric.
- **Naming/migration** (§12) is a recommendation; the delete-vs-keep decision is the owner's call.
- **AutoAscend licensing/attribution** must be preserved when vendoring the root (as v1 did).
- **Behavioral descriptors** (§7) are an initial default, expected to be tuned empirically in M1.

## 16. References

- Supporting research reports (this repo): MAP-Elites & objectives analysis; sibling challenge interface intel; NetHack community insights.
- External: NeurIPS 2021 NetHack Challenge report; BALROG / `nle-progress`; devnull & TNNT tournament milestone ladders; NetHackWiki (3.6.6). Full citations in the community-insights report.
