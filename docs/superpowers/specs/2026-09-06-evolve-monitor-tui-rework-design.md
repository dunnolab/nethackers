# Evolve Monitor — Best-Anchored Rework (harness + TUI)

**Status:** Design — ready for review
**Date:** 2026-09-06
**Author:** vkurenkov (with Claude)
**Extends:**
- `2026-09-02-evolve-union-and-brief-fix-design.md` — the **union cell** (best-on-average). This doc **amends its §5.1**: the union now seeds at **cold-start** from the hub's overall champion, not on iteration 1.
- `2026-08-28-cold-start-per-identity-seeding-design.md` — the `N·b` cold-start cost model. This doc adds **one** full-union eval (→ `2·N·b`), far short of the `(1+P)·N·b` that spec cut.
- `2026-08-15-nethackers-tui-redesign-design.md` / `2026-08-11-nethackers-m3-evolve-tui-design.md` — the run monitor this reworks.
- Reused unchanged: cause-of-death (`2026-08-25`), token metering's 4-kind `TokenUsage` (`2026-08-15`).

**Validated by:** a throwaway clickable Textual prototype iterated ~20 rounds with the user until approved ("finally, this looks good"). The prototype **is** the UX contract; this doc maps it onto the real code and plumbs the data it needs.

**How to read this.** Audience is whoever implements it. Read §2 (mental model) first — the two anchors and the harness/hub split are the invariants. §3 is the glossary; §4 is the decisions ledger (the *why*); §5 is the design (harness plumbing in §5.1–5.6, TUI in §5.7); §6 the data-source map (the delta); §7 the scope boundary; §8 invariants; §9 testing. Grounding line numbers are in Appendix A — re-verify at implementation.

---

## 1. Problem

The evolve run monitor today is tab-oriented: iterations appear only as lines in an agent log, there is no per-identity "what are we actually beating" anchor, and the overall best-on-average program is invisible until (and unless) a lucky local mutation happens to seed the union cell mid-run. A user watching a run cannot see, at a glance: *which hub program is the incumbent for each identity (and who submitted it), which program is best on average across the objective, how the current mutation is doing against those anchors, and why episodes died.*

Two gaps are **display** gaps (the data exists, it just never reaches the TUI) and one is a genuine **harness** gap:

1. **The overall best-on-average program is not established at the start.** `archive.union` is `None` through cold-start (archive.py's union guard rejects cold-start's sub-union slices; loop.py's `base_dev` comment records the full-union seed eval was *dropped*). It only fills later, from a full-coverage **local mutation** — never the hub's actual overall champion. So there is no "BEST OVERALL" anchor to show from iteration 0, and the mutator's `target` is `None` for the first several iterations.

2. **Provenance never reaches the monitor.** Per-identity hub champions *are* pulled and re-evaluated locally at cold-start (so their scores and full per-seed evidence exist), but the submitter handle + commit sha on each `/elites` entry is discarded after seeding.

3. **The rich per-iteration record is disk-only.** `IterationResult` already carries registered/rejected, which cells improved (incl. `"union"`), the 4-kind token `usage`, cause tallies, and `dev_fitness` — but `launch.py` hardwires `on_iteration` to `runlog.append_metric` and never forwards it to the live TUI callbacks.

**What the user wants** (from the request, refined through the prototype):
- After choosing an objective and pressing start, show **the program currently best on average across the chosen identities**, retrieved from the hub *exactly*; if none, **AutoAscend**, clearly labeled.
- **Per identity**, show the **best currently-known hub program** with the **submitter handle + commit sha**, or **AutoAscend** if none.
- Restructure the TUI: a **left iteration list** (init first, then iterations) + right tabs **Progress / Mutator Logs / Logs**. Progress groups identities by role, shows each identity's **best so far** (with handle+sha) and the **live post-mutation average** (seed count + mean). Clicking/keyboard-navigating a cell opens a **live per-seed detail** (seed, score, average, std, cause of death, depth, clickable source link) with a **back button** (no shortcuts).

## 2. Mental model (read first)

**Two anchors, both from the hub, both re-run locally at init.**

- **Per-identity best** — for each identity in the objective, the hub's top `/elites` program (handle + sha). Cold-start already **re-evaluates it locally**, so its score and full per-seed evidence are ours, computed under our own conditions. The hub supplies only the *label*.
- **BEST OVERALL** — the hub's best-on-average program across the objective (the `/board?scope=` macro-average leader). Today it is **not** established at init; this doc **re-runs it locally at cold-start** across all chosen identities to seed the union cell, so it too has a local score + full per-seed×per-identity evidence, and it becomes a real, samplable incumbent from iteration 1.

**The harness/hub split is unchanged (union spec §2, invariants I1–I2).** The harness climbs the fixed public dev block deterministically; generalization is the hub validator's job. This doc adds **no** generalization machinery, **no** new metric, **no** arena/mutator image change.

**Provenance is a label attached to a cell's current elite — not a fixed property.** A cell shows the handle+sha of whoever's program currently holds it. When a local mutation takes the cell, the label **propagates** to `run · iter N`. This is the honest source: the label follows the actual elite, whether that is a hub champion, the overall champion (if it out-scores an identity's own champion locally), a local child, or the seed/AutoAscend floor.

**The monitor never reads the hub for scores it will display as "best so far."** Displayed scores are always **local** measurements (cold-start eval or an iteration's dev eval). The hub is read once, at launch, for *which* programs to pull and for their *labels*. The only exception is the AutoAscend fallback score (`/baseline`), which is a hub-owned reference number with no local per-seed detail (D5).

## 3. Glossary

- **objective / scope** — the identity set the run optimizes, a hub-resolvable token (generalist / a role / a facet-value / a single identity). `selector.resolve` (evolve) and `boards.resolve_scope` (hub board) share resolution.
- **per-identity best** — the hub's top `/elites` program for one identity; after cold-start, the elite of that identity's archive cell.
- **BEST OVERALL / union champion** — the hub's best-on-average program for the scope (`/board?scope=` rank 1); after this doc, the elite of the archive's **union cell**, seeded at cold-start.
- **origin / provenance label** — what a cell's current elite *is*: `hub · <handle>@<sha>`, `run · iter N`, or `AutoAscend`/`seed`.
- **this iteration** — the candidate the current mutation produced; its live per-identity means + seed counts stream in during its dev eval.
- **registered / rejected** — an iteration is *registered* iff its child strictly improved ≥1 cell (identity **or** union); else *rejected* (union spec I4).
- **dev block** — the fixed public seeds (trajectory ids 0–14 per identity) the harness evaluates on; deterministic.
- **`N`, `b`** — objective identity count; per-identity batch size (today 15). Cold-start is `N·b` episodes (cold-start spec); this doc makes it `≤ 2·N·b`.

## 4. Decisions ledger

- **D1 — Per-seed cause/depth/time are delivered, not re-plumbed through the arena.** The live `on_episode` stream carries `{seed, progress, status, turns, depth}`; cause-of-death and wall-time already exist on each completed eval's `TrajectoryResult`s. Deliver those to the TUI via the callback hand-off (§5.5). **No arena/mutator image change.** *Why:* the arena image is the parity boundary (sandbox-image-distribution); the data is already computed, only the delivery is missing. *(User decision this session.)*
- **D2 — Drop xp.** It is captured nowhere in the pipeline (not on `TrajectoryResult`, not in the stream). Milestone/depth already convey run depth. *Why:* adding it means an arena change (blstats read) for a low-value column; excluded by D1. *(User decision.)*
- **D3 — No "limited hub detail" case; hub programs are re-run locally at init.** Because cold-start re-evaluates every pulled hub program (per-identity champions today; the overall champion after §5.1), every "best so far" is backed by **local** per-seed evidence. The hub supplies only the handle+sha label. *Why:* uniform, honest detail for all local anchors; resolves the "hub stores no per-seed cause/depth for others' programs" gap by never depending on it. *(User's note this session.)*
- **D4 — Seed the union at init from the hub's overall champion.** Pull `/board?scope=` rank 1, resolve its tree, evaluate it once on the full union batch, insert → seeds `archive.union`. *Why:* the user's requirement (BEST OVERALL shown from the start, full detail) and a loop improvement (the highest-value parent + the mutator `target` are available from iteration 1 instead of `None`). *Cost & behavior change accepted by the user this session* (§5.1).
- **D5 — AutoAscend fallback is label + `/baseline` score, aggregate-only detail.** Where the hub has no champion (a whole empty scope, or a single uncovered identity), show `AutoAscend` with its `/baseline` score; its detail view shows the per-identity baseline average with a *"baseline · no per-seed breakdown"* note, because AutoAscend is a hub-baseline number, not a local program tree to re-run. *Why:* honest; an edge case (the live hub covers every identity). *(User approved the recommended option.)*
- **D6 — `on_iteration` is the single delivery lever for provenance, union movement, per-kind tokens, and causes.** Forward the existing `IterationResult` to the live TUI (in addition to disk). *Why:* one wire delivers items that would otherwise each need bespoke plumbing; no new harness computation.
- **D7 — The prototype is the UX contract; reuse `RunMonitor`/`Run`/`status.py`.** Port the validated widget behavior (§5.7) onto the existing files rather than a new screen subsystem. *Why:* the interactions are already validated; the risk is in the data plumbing, not the layout.

## 5. Design

### 5.1 Init: seed the union cell from the hub's best-on-average champion

**Amends union spec §5.1** (which had the union seeding on iteration 1's first full dev eval). After the existing per-identity cold-start (unchanged — cold-start spec), add one step:

1. **Fetch** the overall champion: `hub.board(scope, tier=…)` **rank-1** row for the run's objective, where `scope` is the objective token and `tier` matches the per-identity cold-start (`self-reported` by default — keep the two tiers identical so the anchors are comparable). The macro-average board (boards.py) ranks by **coverage desc, then macro-average-of-per-identity-means desc** — so rank-1 is the scope's leaderboard leader: the best-on-average program among the maximal-coverage ones (exactly what the hub serves as "the best" for the scope). Under uniform `b` per identity, that macro-average equals the pooled union mean (`dev_fitness`), so re-evaluating rank-1 locally on the full union batch (step 3) yields a `union_mean` consistent with the hub's ranking. The row carries `owner` (handle), `reference{repo,commit}` (sha + link), `program_id`, `mean_progression` (its hub score, shown until the local eval lands).
2. **Resolve** its tree through the local cache (the same `_resolve`/`pull_fetch` path `per_identity_elites` uses).
3. **Evaluate** it once on the **full union batch** `build_union_spec(all identities)` — `N·b` episodes — and `archive.insert(...)`. Full-coverage evidence seeds `archive.union` (and may improve any identity cell it out-scores locally — harmless and correct).
4. **Record its origin** as `hub · <owner>@<sha>` (§5.2).

**Effects.** BEST OVERALL is shown from iteration 0 with a local score + full per-seed×per-identity detail; `_pick_cell` can draw the union (weight 2×) from iteration 1; the brief's `target` is set from the start. **These are real behavior changes to early search, accepted (D4).**

**Cost.** One extra full-union eval → cold-start becomes `N·b (per-identity) + N·b (union champion) = 2·N·b`. Negligible for a facet, ~2× for a role, heavy only for generalist (~+1095 episodes). This is **not** a return to the `(1+P)·N·b` the cold-start spec removed — it adds exactly **one** program's union eval, independent of `P`. Optional fold-in (defer): where the overall champion coincides with a per-identity champion, skip the duplicate identity evals; marginal, not worth the branching initially.

**Degenerate cases.** (a) **Single identity (`N=1`)**: `archive.union` is not used (the union guard requires `N>1`); BEST OVERALL collapses to that identity's best — the TUI shows the one identity and omits a separate BEST OVERALL row. (b) **Empty board** (no champion for the scope): skip the union seed; BEST OVERALL displays AutoAscend + `/baseline` overall (D5); the union cell fills later from a local child exactly as today. (c) **`--from-seed`**: hub is out; no union seed pull; unchanged.

### 5.2 Provenance: an origin label per cell

The archive stays **pure data** (archive.py's contract). The **loop** maintains `origins: dict[str, Origin]` keyed by program digest, where `Origin` is `(kind: "hub"|"run"|"seed", handle: str|None, sha: str|None, repo: str|None, iteration: int|None)`:

- **cold-start, per-identity**: for each pulled `/elites` entry, `origins[program_id] = Origin("hub", owner, commit, repo, None)`.
- **cold-start, overall champion**: same, from the `/board` row (§5.1).
- **cold-start, seed/uncovered**: `origins[seed_digest] = Origin("seed", …)` (rendered as AutoAscend/seed per D5).
- **registration**: when a child registers, `origins[child_digest] = Origin("run", owner, sha?, repo?, k+1)`.

The label the TUI shows for a cell is `origins[cell.digest]`. Because `insert` re-points a cell to whichever digest currently holds it, the label **propagates for free** (a cell taken by iteration `k` now resolves to `run · iter k`). Delivery to the TUI: the initial map via the `on_state` "cold-start" payload; updates via the wired `on_iteration` (`IterationResult.improved` names the cells, `digest` names the new elite). *(Confirm at implementation whether to thread `origins` on the state payload or add a small dedicated field; either is fine — it is a `{digest: origin}` dict.)*

### 5.3 The `on_iteration` lever

Change `launch.py`'s `run(callbacks)` so `on_iteration` **also** forwards to the TUI:

```python
on_iteration=lambda it, res: (
    runlog.append_metric(run_dir, runlog.metric_record(it, res)),
    callbacks.get("on_iteration", lambda *_: None)(it, res),
)
```

Add `on_iteration` to the TUI's callback dict (app launch path) and have `Run` fold each `IterationResult` into per-iteration state. This delivers, per iteration: **registered/rejected** (`registered`, `reason`), **which cells improved incl. `"union"`** (`improved` → BEST OVERALL update + per-identity propagation), **per-kind tokens** (`usage`), **cause tally** (`causes`), **overall score** (`dev_fitness`), and the new elite **digest**. The disk metric log is unchanged.

### 5.4 Per-kind tokens + operator version (the title/subline)

- **Per-kind tokens (4 kinds).** `TokenUsage` carries input/output/cache_creation/cache_read; the subline shows all four (`.spend` = input+output+cache_creation excludes the ~10%-billed cache_read, which would inflate the visible total ~40×). Add a `Run` accessor that sums `IterationResult.usage` across iterations (delivered by §5.3) into the four kinds. No harness change beyond §5.3.
- **operator_version.** Resolved at launch and written to `run.json`, but absent from the live `EvolveConfig`. Add `operator_version` to `EvolveConfig` (status.py) and populate it in `prepare_evolve` (launch.py `cfg = EvolveConfig(...)`). The mutator title then shows **coding agent + version + model + effort** live.
- **Total wall time.** `Run.run_time()` already exists; render as h/m (no seconds).

### 5.5 Per-seed detail: live stream + completed evidence (no arena change)

Two states, per D1:

- **Currently-evaluating child (live):** the `on_episode` stream fills seed / progress / status / turns / depth as episodes land; cause-of-death and time render `—` (pending). std over the seeds so far.
- **Any completed evaluation** (a done iteration's child, or an init cell): the full per-seed `TrajectoryResult`s — seed, progress, status ∈ {died, ascended, timed out}, **cause_of_death**, **milestone/depth**, turns, **wall_seconds** — already exist (`dev_ev.results` for a child; each cell's `dev_evidence.results` for cold-start). Deliver them to the live `Run`:
  - **children**: add the per-seed result dicts to the iteration hand-off (extend `IterationResult` with `results: list[dict] | None`, or a parallel argument on the wired `on_iteration`), so a completed iteration carries its per-seed detail.
  - **init cells**: carry each cold-start cell's `dev_evidence.results` in the `on_state` "cold-start" payload.
  - std is computed in the TUI across the seeds; **xp is dropped** (D2; absent from `TrajectoryResult`).
- **Source link** (clickable): for a `run` origin, the local worktree/tree path; for a `hub` origin, `reference.repo@commit` (a GitHub URL). Both come from the origin (§5.2).

Durable per-seed detail for a **reopened** run reuses the persisted per-attempt eval (`refs.assemble` already writes `eval.json`) — the live callback path is primary; reopened-run reconstruction is confirmed at implementation and, if a gap exists, is a small follow-up (out of scope to re-architect here).

### 5.6 AutoAscend fallback (D5)

Where a cell has no hub champion (uncovered identity) or the whole scope has no board rows (BEST OVERALL): label **AutoAscend**, score from `hub.baseline()` (per-identity for a cell; overall for BEST OVERALL). The detail view shows the baseline average with *"baseline · no per-seed breakdown"* — AutoAscend is not a local tree, so there is nothing to re-run. `/baseline` is the only hub-served score the monitor displays (everything else is a local measurement, §2).

### 5.7 TUI structure (the validated prototype → real widgets)

A straight port of the approved prototype onto `tui/screens/monitor.py` `RunMonitor`, `tui/run.py` `Run`, and `status.py`. No open UX questions — these were settled in the prototype.

- **Layout.** Left **iteration list** (row 0 = init, then one row per planned iteration; each marked `registered` / `rejected` / `live` / inactive-dim; only done/running rows clickable). Right **3 tabs**: **Progress**, **Mutator Logs**, **Logs**. Selecting an iteration drives all tabs (master-detail); the running iteration updates live. An embedded, toggled **detail view** overlays the tabs when a cell is opened (not a pushed screen — see the Textual gotcha).
- **Progress tab** — one framed `DataTable` ("progress by identity"): **row 0 = BEST OVERALL** (the union cell; §5.1), then identities **grouped by role** (only roles present in the objective). Columns: `identity | best so far | this iteration`. `best so far` = the cell's current elite label+score (`hub · handle@sha` / `run · iter N` / `AutoAscend`), propagating (§5.2). `this iteration` = the live candidate mean + seed count (`— · no mutation` for init). `✎` marks the sampled parent cell (from `on_state cell=`). `best so far` and `this iteration` are **different programs** → each opens its own per-seed detail.
- **Only clickable cells highlight.** Port `ClickTable`: cursor-follows-mouse (`_on_mouse_move` moves the cursor onto a cell only if `_valid_fn(row,col)`), DataTable's own hover disabled (`_set_hover_cursor` → always False), single-click opens (`_on_click` → `_post_selected_message`), keyboard arrows navigate the same clickable cells (cursor-snap on `CellHighlighted`). Live updates patch cells **in place** (no full rebuild) to avoid highlight flicker.
- **Init row** only **evaluates** pulled programs — no mutation: no `✎`, no mutator log, no registered/rejected, `this iteration = — · no mutation`.
- **Detail view** — full per-seed table (§5.5) with a **back button** (no shortcuts). BEST OVERALL opens the **full** table (every seed × every identity); a per-identity cell opens that identity's seeds.
- **Title / subline.** Title = mutator (agent + version + model + effort; §5.4). Subline = 4-kind tokens + total wall time (§5.4). Wording: "seed" not "episode"; no "build(s)"; "this iteration" not "this run".
- **Textual 8.2.8 gotcha.** Full-region render (`save_screenshot`, some refreshes) can hand `Visual.to_strips` a `None` background for a pushed screen / toggled panel → crash. Use the embedded toggled widget (not a pushed screen) and keep the module-level `Visual.to_strips` None→blank-strips guard as a fallback. Verify looks via the screenshot pipeline (render → SVG → `qlmanage` PNG → inspect).

## 6. Data-source map (the delta)

| Display element | Source | Status |
|---|---|---|
| Per-identity best **score** | local cold-start eval (archive cell score) | ✓ exists |
| Per-identity best **handle@sha + link** | `/elites` entry `owner` / `reference.commit` / `reference.repo` | plumb (§5.2) |
| **BEST OVERALL** score + handle@sha | `/board?scope=` rank-1 row, re-run locally | new eval + plumb (§5.1) |
| BEST OVERALL / per-identity **propagation** to `run · iter N` | `IterationResult.improved` (incl. `"union"`) | wire `on_iteration` (§5.3) |
| **registered / rejected** per iteration | `IterationResult.registered` / `reason` | wire `on_iteration` |
| `✎` **sampled parent** cell | `on_state` `cell=` | ✓ exists |
| **this iteration** live mean + seed count | `on_episode` → `Run.candidate_means` | ✓ exists |
| **per-kind tokens** (4 kinds) | `IterationResult.usage` (`TokenUsage`) | wire + `Run` accessor (§5.4) |
| **total wall time** | `Run.run_time()` | ✓ exists |
| **mutator** agent+version+model+effort | `EvolveConfig` (+`operator_version`) | add field (§5.4) |
| per-seed **seed/progress/status/turns/depth** (live) | `on_episode` stream | ✓ exists |
| per-seed **cause_of_death / wall_seconds / milestone** (completed) | `TrajectoryResult`s via hand-off | plumb (§5.5) |
| per-seed **xp** | — | dropped (D2) |
| per-seed **std** | computed in TUI across seeds | ✓ derived |
| **source link** | origin: local path or `reference.repo@commit` | ✓ from §5.2 |
| AutoAscend fallback score | `hub.baseline()` | ✓ exists (D5) |

## 7. Scope boundary

**Touches:** the harness cold-start (add the union-champion eval + origins), the `on_iteration` wire in `launch.py`, `IterationResult` (carry per-seed results + reuse existing fields), `EvolveConfig` (`operator_version`), and the TUI (`monitor.py`, `run.py`, `status.py`).

**Does NOT touch:** the arena or mutator images (D1); the objective catalog / batch / seed set (no hub DB wipe); the deterministic eval or fitness metric; acceptance logic (strict `>`, union included — unchanged from union spec); any hub schema or endpoint (reads existing `/board`, `/elites`, `/baseline`); the hub validator / any generalization machinery.

## 8. Invariants (check changes against these)

- **I1.** Displayed "best so far" and "this iteration" scores are **local** measurements; the only hub-served displayed score is the AutoAscend `/baseline` fallback.
- **I2.** A cell's label is the origin of **whatever digest currently holds it** — it propagates to `run · iter N` when a local child takes the cell.
- **I3.** Cold-start cost ≤ `2·N·b` episodes (per-identity `N·b` + one union-champion `N·b`), independent of `P`. `--from-seed` and empty-board skip the union eval.
- **I4.** No arena/mutator image change; no new eval metric; xp is absent everywhere (not silently zero-filled).
- **I5.** Union acceptance/selection semantics are unchanged from the union spec (I3–I7 there): union score ≡ `dev_fitness`, `registered ⇔ ≥1 cell incl. union improved`, union weighted 2×. The **only** change is *when/whence* the union first seeds (cold-start, from the hub overall champion).
- **I6.** The monitor reads the hub only at launch (which programs + labels) and for the `/baseline` fallback — never per-frame.
- **I7.** Init evaluates only; it shows no mutation, mutator log, `✎`, or registered/rejected.
- **I8.** For `N=1`, no separate union/BEST OVERALL cell exists.

## 9. Testing

**Harness (unit, existing patterns):**
- Cold-start seeds `archive.union` from the `/board` rank-1 program: one extra full-union eval issued; the union elite's score = its local union mean; total episodes `= 2·N·b` (or `N·b` when the overall champion coincides with the sole seed / on `--from-seed`).
- Empty board → no union eval; union stays `None`; BEST OVERALL resolves to the AutoAscend fallback.
- `N=1` → no union cell; no extra eval.
- Origins: a hub-seeded cell resolves to `hub · handle@sha`; after a registering child takes it, the same cell resolves to `run · iter N`; the seed cell resolves to AutoAscend/seed.
- `on_iteration` forwards to the TUI callback **and** still appends the disk metric (both fire).
- `IterationResult` carries per-seed results for a completed child; a rejected-but-scored child carries them too (for its detail view).
- `EvolveConfig.operator_version` is populated from the resolver and surfaces on `cfg`.

**TUI (mount + screenshot, per the verify-looks memory):**
- Progress table renders BEST OVERALL row + role groups; only clickable cells highlight (cursor-follows-mouse); single-click opens; arrows navigate clickable cells only; live updates patch in place without highlight flicker.
- Init row shows `— · no mutation`, no `✎`, no mutator log.
- Opening `best so far` vs `this iteration` opens **different** programs' detail; BEST OVERALL opens the full seed×identity table.
- Detail view: live fill of seed/progress/status/turns/depth; cause/time fill on completion; std computed; source link resolves (local vs github); back button returns (no shortcut).
- Title shows agent+version+model+effort; subline shows 4-kind tokens + h/m time.
- AutoAscend fallback cell shows the baseline note in detail.
- Textual full-render (screenshot) does not crash.

## 10. Rollout / build order

Harness runs from source; **no image rebuild, no hub deploy** (reads existing endpoints). Suggested order, each with tests before the next:
1. `on_iteration` wire (§5.3) + `Run` folds `IterationResult` — unlocks registered/rejected, propagation, per-kind tokens.
2. Origins map (§5.2) + `EvolveConfig.operator_version` (§5.4).
3. Union seed at cold-start (§5.1) — the one behavior change; test cost + degenerate cases.
4. Per-seed result hand-off (§5.5).
5. TUI rework (§5.7) onto `monitor.py`/`run.py`/`status.py`; verify via screenshots.

A CLI release for pip users can follow once validated locally (harness-only; hub untouched).

## 11. Out of scope (deferred)

- The fold-in optimization for the union eval (§5.1) — ship the simple `2·N·b` first.
- Reopened-run full per-seed reconstruction beyond what runlog/eval.json already persist (§5.5) — confirm; small follow-up if needed.
- xp anywhere (D2); any arena/mutator image change (D1).
- Any hub schema/endpoint change; the hub validator / generalization layer.
- Selection diversity, multi-exemplar briefs, population-per-cell — inherited deferrals from the union / exploration specs.

## 12. Open decisions (to iterate after review)

- **Origins delivery shape (§5.2):** thread `{digest: origin}` on the `on_state` payload vs a small dedicated field — either works; pick at implementation.
- **Per-seed results carrier (§5.5):** extend `IterationResult.results` vs a parallel `on_iteration` argument — pick the one that keeps `metrics.jsonl` lean (favor not bloating the durable metric log).

---

## Appendix A — Verified codebase facts (re-confirm exact lines at implementation)

- **Union cell is empty at cold-start; full-union seed eval was dropped.** `harness/archive.py` `CellArchive.insert` seeds the union only on full-coverage evidence (`set(identities).issubset(results' characters)`), which cold-start's sub-union slices never satisfy; `harness/loop.py` `base_dev` comment records the drop; `_pick_cell` treats `union is None` as uniform-over-identities. So `archive.union is None` until a full-coverage local child (loop.py brief `target` "None until the union cell is seeded").
- **Per-identity champions ARE re-run locally at cold-start.** `harness/loop.py` cold-start iterates `owned` (champion → identities), `evaluate(tree, build_union_spec(idents), …)`, `archive.insert` — full `ev.results` captured per cell.
- **`IterationResult` already carries the needed fields.** `harness/loop.py`: `registered`, `reason`, `dev_fitness`, `tokens`, `usage: TokenUsage`, `digest`, `causes`, `hub_reason`, `improved: list[str]` (includes `"union"`). Built at both registered and rejected paths.
- **`on_iteration` is disk-only.** `harness/launch.py` `run(callbacks)`: `on_iteration=lambda it, res: runlog.append_metric(...)`; the TUI callback dict has `on_episode`/`on_state`/`on_log`/`stop` but not `on_iteration`.
- **`operator_version` resolved but not on `cfg`.** `harness/launch.py`: resolved via `_default_operator_version`, written to `run.json`; `EvolveConfig(...)` omits it.
- **Provenance fields.** `/elites` row (`hub/views/elites.py`): `{identity, program_id, owner, score, reference:{repo, commit}}`. `/board` macro-average row (`hub/views/boards.py` `_finalize_row` + macro-average path): `{rank, program_id, reference:{repo, commit}, owner, mean_progression, median_progression, coverage, …}`. `boards.resolve_scope` mirrors `selector.resolve`.
- **Hub client.** `hubclient/client.py`: `elites(scope, tier="self-reported")`, `board(scope, tier)`, `baseline()` — all return unwrapped rows. `harness/select.py` `per_identity_elites` returns `{ident: (entry, tree_path)}` with the `/elites` entry intact.
- **Live episode stream fields.** `hubclient/live.py`: `{seed, character, progress, status, turns, depth, index, total}` — **no** cause_of_death, wall_time, xp, or std.
- **Per-seed completed detail.** `contracts/models.py` `TrajectoryResult`: has `cause_of_death`, `wall_seconds`, `milestone`; **no xp**. Held on `archive.py` `Cell.dev_evidence` and each child's `dev_ev` (loop.py dev eval), serialized by `refs.assemble` per attempt.
- **Cost baseline.** `2026-08-28-cold-start-per-identity-seeding-design.md`: cold-start is `N·b` (was `(1+P)·N·b`); this doc adds one union eval → `2·N·b`.
