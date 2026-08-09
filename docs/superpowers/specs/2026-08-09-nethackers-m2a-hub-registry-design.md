# NetHackers M2a — Hub Registry (thin, tier-1) — Design Spec

- **Date:** 2026-08-09
- **Status:** Draft for review
- **Builds on:** the main design spec [`2026-08-08-nethackers-design.md`](2026-08-08-nethackers-design.md) (§5 tiers, §7 archive, §8 primitives, §10 the hub) and M1 (merged: the pinned arena image, the `Evidence` contract, `hubclient` stubs).
- **Grounded by (this cycle's analyses):** [`../research/2026-08-09-progress-measurement-structure-analysis.md`](../research/2026-08-09-progress-measurement-structure-analysis.md), [`../research/2026-08-09-nethack-eval-cost-and-variance.md`](../research/2026-08-09-nethack-eval-cost-and-variance.md), [`../research/2026-08-09-distributed-evolution-dynamics-incentives.md`](../research/2026-08-09-distributed-evolution-dynamics-incentives.md), and [`../research/2026-08-09-github-oauth-vs-apps.md`](../research/2026-08-09-github-oauth-vs-apps.md).

---

## 1. Context & scope

M2 (the hub) is split into three sub-milestones: **M2a** (this spec — the thin registry + read/write API + derived views, tier-1 self-reported, local), **M2b** (verification workers: tier-2 replay + tier-3 held-out), **M2c** (public website + deployment). This spec is **M2a only**.

Three analyses this cycle reshaped the design in two ways that this spec bakes in:
1. **The "one map" was really four distinct objects.** M2a is built on that clean separation (§3).
2. **Regime discipline.** At the realistic scale this will run (single-digit-to-low-tens sustained contributors, power-law effort), anti-gaming / reputation / verification machinery is nearly irrelevant; the binding constraints are the search working and retention. So M2a is **deliberately thin**: self-reported evidence, labeled, no verification, no website, no anti-gaming. Trust comes later, cheaply, via determinism (M2b).

## 2. Goals & non-goals

**Goals**
- A thin registry: contributors register **per-episode evidence + a solution reference**; the hub stores it and derives the public views. It stores links + numbers, never candidate bytes.
- Close the coordination loop end-to-end on **tier-1 self-reported** evidence: `register` → the attainment record / elite pool / boards update → `elites`/`pull` feed the next search.
- Comparable tier-1 evidence: the hub publishes each objective's fixed `(seed, character)` batch; everyone evaluates on the same atoms.
- Safe identity without a broad credential: **GitHub App device-flow** auth (§6).
- Container-parity: the whole hub runs locally via `docker compose`, driven by fixtures.

**Non-goals (deferred — see §11)**
- No verification (tier-2 replay / tier-3 held-out), no held-out secret, no App private key → **M2b**.
- No public website / hosting → **M2c**.
- No reputation, no anti-gaming machinery, no board proliferation. Self-reported evidence is labeled `self-reported`; that is the whole trust story in M2a.

## 3. Core model — one substrate, three derived views, and functionals

The central correction from the structure analysis: **do not conflate distinct objects into "one map."** M2a stores one substrate and derives three views with **different contracts**.

**Substrate — Atoms.** An **atom** is one episode's result:
`atom = (identity, seed, horizon) → (progression, milestone_reached, ascended, status, turns, steps)`.
Evidence is a **bag of atoms**. `register` stores atoms; **everything else is derived from atoms** — "objectives don't fill anything; evidence does."

**Derived view 1 — Attainment record** (the public "map"). **Append-only, monotonic.** For each `(identity, milestone)`: the set of solutions/owners that have reached it, and the **first** to do so. Serves progress-tracking, coverage, and "firsts" credit. Trustworthy by construction: existence claims only ratchet up, and each is checkable by a single deterministic replay (in M2b). **Pinned meaning: an attainment record (a bot trophy grid), not a MAP-Elites archive.** (The richer "goal-conditioned sub-tasks" reading is a future upgrade — §11.)

**Derived view 2 — Elite pool** (the gene pool the *search* pulls). **Top-k current-best solutions per identity** (displaceable, not monotonic). What `elites` returns; what evolution imports as parents. **Top-k, not top-1, deliberately** — a single global best-per-cell that everyone pulls creates monoculture/takeover (dynamics analysis); top-k + spread sampling keeps diversity. This is a genuinely different object from the attainment record (current-best vs ever-reached).

**Derived view 3 — Boards.** A **grading functional + a tier filter, rendered as a ranking.** Not stored — a query over atoms. Coverage/firsts boards are queries over the attainment record.

**Objectives = grading functionals.** An **objective** is a declared functional `(which atoms, weighting, aggregation)` applied to atoms → one comparable number. This is the precise meaning of "objective" (it grades; it does not organize storage). The aggregation is an explicit choice (mean, IQM, lexicographic ascensions-first, …); M2a's headline `random` uses `ascensions → median → mean` over its batch (matching the NeurIPS challenge shape).

| Object | Contract | Role |
|---|---|---|
| **Atoms** | immutable measured facts | the only stored substrate |
| **Attainment record** | append-only, monotonic | public progress + coverage + firsts |
| **Elite pool** | top-k current-best per identity, displaceable | the search's gene pool (`elites`) |
| **Boards** | a functional + tier filter, computed on read | rankings |
| **Objectives (functionals)** | declared (atoms, weighting, aggregation) | how atoms are graded |

## 4. Objectives catalog & published batches

The hub owns a **fixed objective catalog**, each with a canonical **digest** over `(character distribution, horizon, published batch)`:
- `random` — the canonical headline; a **fixed, frozen batch of `(seed, character)` pairs** sampled once to match NetHack's natural draw.
- each of the ~73 **identities** — a batch of that one character across seeds.
- `all` and role/race/alignment/gender **slices** — functionals (queries) over the relevant identities.

**Every objective's batch is an explicit, published list of `(seed, character)` pairs** (not "let NLE pick randomly" — comparability is guaranteed by construction, not by NLE's RNG). `nethackers eval <solution> --objective <name>` fetches the batch from the hub and evaluates exactly those `(seed, character)` pairs → tier-1 atoms carrying the objective digest. (Cost is affordable: the cost analysis measured `random` at a few hundred games ≈ minutes–an evening; a "handful of seeds" is a **cull, not a ranking** — a cell needs N≈30 on canonical seeds before its ranking is meaningful.)

## 5. Storage (SQLite)

- `solutions(digest PK, repo, commit, owner, root, entrypoint, registered_at)`
- `lineage(child_digest, parent_digest, kind ∈ {parent, influence})`
- `atoms(id PK, evidence_digest, solution_digest→, objective_digest→, owner, tier, evaluator_image, identity, seed, horizon, progression, milestone, ascended, status, turns, steps, created_at)` — the substrate; `tier` is always `self-reported` in M2a, present so M2b's tiers slot in.
- `objectives(digest PK, name, kind, character_spec, horizon, batch)` — the catalog + published `(seed, character)` batches.
- Derived-view tables (materialized on write): `attainment(identity, milestone, first_solution, first_owner, first_at)` + a holders index; `elite_pool(identity, rank, solution_digest, score)`. Boards are computed on read from `atoms`.

## 6. Write path (`register`) + auth

**Auth — GitHub App, device-flow only** (from the OAuth-vs-Apps analysis): the CLI authenticates via the NetHackers **GitHub App's device flow** → a short-lived, minimal-scope **user token** (never the personal `gh` token). The hub calls GitHub `GET /user` → `login`, and confirms the registered repo is `github.com/<login>/nethacker`. **M2a holds no GitHub secret** (public client ID only); the App private key / installation tokens are M2b (private repos, workers). Auth is a **pluggable `AuthProvider`**: `LocalStubAuth` (a configured dev identity — no GitHub calls, drives fixtures/tests) and `GitHubAppAuth`.

**Validation ladder — all cheap, no code execution:**
1. **Identity** — token valid → `login`; repo owner == `login`.
2. **Reference exists** — `repo@commit` resolves; `commit` is a real 40-char SHA.
3. **Manifest well-formed** — `nethackers.solution.json` at that commit parses/validates (known `root`, valid `parents`/`influences` or empty, `entrypoint`).
4. **Digest matches** — shallow-fetch the solution subtree at `repo@commit`, recompute the content digest, check equality (a git fetch, never running the code).
5. **Atoms well-formed** — `objective` digest is a catalog objective; the submitted `(seed, character)` set **equals that objective's published batch**; metrics finite; **evaluator-image digest present** (M1 follow-up: `eval` records the image *digest*, not the tag); `tier == self-reported`.
6. **Store & recompute** — upsert solution + lineage + atoms; incrementally update the attainment record (identities/milestones the atoms reached), the elite pool (identities touched), and any cached board aggregates.

Idempotent by evidence digest; rate-limit per `login`.

## 7. Read API + CLI

**One FastAPI service**; reads are **public/no-auth** over the derived views, writes are `register`. Reads hit materialized views — nothing computes per request. Responses are references + summaries; bytes come from GitHub via `pull`.

- `GET /objectives`, `GET /objectives/{name}/batch` — discover objectives + fetch the published `(seed, character)` batch.
- `GET /attainment[?identity=]` — the attainment record (the "map"): who reached each `(identity, milestone)`, and firsts.
- `GET /elites?objective=X` — the elite pool (top-k per identity, or a rollup for `random`/`all`/slice).
- `GET /board?objective=X` — a functional's ranking.
- `GET /solutions/{digest}` (`show`), `GET /search?...` (paginated).

**CLI** (over `hubclient`): `nethackers map` (renders the attainment record), `elites --objective X`, `board --objective X`, `search`, `show`, `pull <digest|repo@commit>`, and writes `register` / `publish`. CLI renderers are pure functions of read responses (fixture-drivable; the M2c website will render the same data). Hub URL via `--hub` / env, default `http://localhost:8000`.

## 8. Tech, package structure, container-parity

Python + **FastAPI** + **SQLite**, reusing `nethackers.contracts`.

```
src/nethackers/hub/
  api.py            # FastAPI: read endpoints + POST /register
  store.py          # SQLite: atoms + solutions + lineage; upsert/queries
  atoms.py          # Atom type; Evidence → atoms
  functionals.py    # objectives as grading functionals (catalog + aggregation)
  seeds_catalog.py  # published fixed (seed, character) batches per objective
  auth.py           # AuthProvider: LocalStubAuth | GitHubAppAuth (device flow)
  validate.py       # the register validation ladder
  views/attainment.py · views/elites.py · views/boards.py
hub/Dockerfile       # + a compose service
fixtures/hub/         # shared sample dataset
```
Plus the M1 `hubclient` stubs fleshed out, the new CLI subcommands, and the `render` extension for the CLI views. `docker compose up` runs **`api` + a SQLite volume** (workers = M2b, website = M2c). One shared fixture dataset drives the API tests, the CLI renderer previews, and (later) M2c.

## 9. Testing

- **Unit:** each validation-ladder check; the three view computations against fixtures with known expected outputs — *especially* the differing contracts (attainment is monotonic/append-only; elite pool is displaceable top-k; boards are pure functionals); store upsert/dedup/idempotency.
- **API integration** (local, `LocalStubAuth`, temp SQLite): `register → attainment/elites/board/search`, incl. recompute-on-write and rejection cases (bad digest / wrong batch / unknown objective / non-tier-1 / non-monotonic attainment regression).
- **`GitHubAppAuth`** device-flow token→login + own-repo check against a **mocked** GitHub API (real App at deploy, M2c).
- Extend CI (fast `pytest` + `mypy` + `ruff`) to the `hub` package.

## 10. Scope boundary

- **In M2a:** the substrate (atoms) + the three derived views + grading functionals; the objective catalog + published batches; `register` + read API + CLI; GitHub-App device-flow auth (pluggable, stub locally); SQLite; `docker compose`; fixtures; evaluator-image *digest* in evidence.
- **→ M2b:** tier-2 replay (**must verify *trace-given-program*, not score-given-trace**) + tier-3 held-out; the held-out secret + App private key/installation tokens; the tier-upgrade-in-place flow; **seed-set governance / rotation** (the generality guard — tiers 1–2 cannot catch an *honest* overfit to public seeds).
- **→ M2c:** the public website + hosting + the registered App; richer boards (conducts/streaks/turncount/YASD).

## 11. Assumptions & open questions

- **Trust in M2a is determinism + labeling, nothing more.** Self-reported atoms are labeled; anyone can reproduce them (M1 container-parity). Real verification is M2b. This is a conscious regime bet (small scale ⇒ gaming is not the binding constraint).
- **Elite pool = top-k, not top-1** — to avoid the shared-pool monoculture/takeover the dynamics analysis flagged. `k` and the spread-sampling policy are tunable.
- **Attainment record is pinned as an attainment record (reading B).** The "goal-conditioned sub-tasks" reading (per-`(identity, milestone)` reliability-then-speed tasks) is a deferred upgrade, not M2a.
- **Empirical questions to watch (from the analyses), not blockers:** does GigaEvo's combine-complementary crossover actually pay at AutoAscend scale; milestone spacing at the Castle→Gehennom gap; the public-seed overfitting floor; elite-pool takeover diagnostics.
- **Parked design items (revisit if/when they bind):** prefix-replay springboards *if* the frontier stalls at a reachable-but-hard gate (late game is otherwise a natural time-ordered stage, not a gap); retention/churn mechanisms, anti-herding pull policy, recursive impact credit, evaluation economics — all **"if it goes viral."**

## 12. References

- Main design spec (this repo); the four 2026-08-09 research reports (structure, cost/variance, dynamics/incentives, GitHub OAuth-vs-Apps).
- External: Multi-task MAP-Elites (Mouret & Maguire 2020); GigaEvo (arXiv:2511.17592); NeurIPS 2021 NetHack Challenge report; TNNT; BALROG / `nle-progress`. Full citations in the research reports.
