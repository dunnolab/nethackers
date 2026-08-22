# Generalist objectives: evolve one bot across a set of identities

**Status:** design · **Date:** 2026-08-22 · **Branch:** `m3/generalist-objectives`

## Summary

Today `evolve <objective>` optimizes against a **single** identity (or the
broad `random`). This adds **generalist objectives**: evolve **one** bot
graded across a **set** of identities — "all Wizard builds," or an arbitrary
hand-picked set — with a single aggregated fitness. The set is named by a
**selector** on the CLI/TUI; the loop grades the bot on the union of the
members' episodes, steers the mutator at the weak builds, and the evolve
window is redesigned to show the per-identity picture.

This needs **no hub catalog change** and **no DB wipe**. A generalist
objective is a *query* over the per-program result vector the hub already
stores, and wins are registered as ordinary **per-identity slices**.

## The model (settled with the user)

- The hub already stores, per program, a (sparse) **vector over the 73
  identities** — its per-identity result, or **MISSING** where never
  evaluated (physically: per-identity atoms, `hub/atoms.py`).
- An **objective is a subset `S`** of those identities: single (`|S|=1`),
  a role (`|S|≈10`), `all` (`|S|=73`), or an arbitrary list. Ranking /
  selection for `S` is a query that aggregates each program's vector over
  `S`. `all` is already this query over the full set (`boards.py`), just a
  crude rollup today; this is the clean, subset-parameterized version.

### Metric (decision C — floor-aware, stable gate)

- **Gate on the union average.** Accept a candidate when its mean progression
  over the union of `S` strictly improves on the parent (and likewise on the
  held-out validation union) — the existing two-gate pattern, applied to the
  union. `evaluate()` already returns `mean_progress` over the whole batch,
  so the union mean is free.
- **Floor first-class, but *reported*, not gated.** The per-identity
  breakdown (each build's mean, the weakest = the floor, coverage) is
  surfaced in the brief and the evolve window, and any per-identity
  **regression** vs the parent is flagged in the ledger. It is **not** a hard
  gate: a strict no-regression gate needs a magic ε and reintroduces the
  "0 wins" failure. (ε-gating is a deferred option, not MVP.)

Rationale: the accept/reject decision stays on a stable, low-variance signal
(union mean); the thing a human/paper cares about (breadth, the floor) is made
visible and steers the mutator, without betting acceptance on a noisy min.

### Selection (coverage-gated for the MVP)

Two reads over the vector, **different** correct missing-value policies:

- **Elites(S)** — leaderboard / mid-run migration target. **Coverage-gated:**
  rank only programs that cover **all** of `S`, by the union average. Missing
  is never imputed.
- **Influence(S)** — the parent pool for SELECT. *Also coverage-gated for the
  MVP* (simple, honest). Its known limitation — distributed single-identity
  specialists get gated out, so a fresh role run cold-starts from the seed —
  and the smarter design (coverage-aware influence, verifier-trust weighting)
  are tracked in **issue #12** and are explicitly out of scope here.

Coverage-gated selection is implementable **client-side** by fanning out over
the members' existing per-identity elite queries and intersecting — no hub
endpoint change (see Components §7). Falls back to the seed when nothing
covers `S`, which is exactly today's behavior.

### Mutator (option a — trust it)

The mutator container has live NLE + the sealed scorer kit and self-tests on
the **training seeds handed to it in the brief** (info-diet wall §3.6 intact —
it never sees held-out seeds). For a generalist it is handed the whole set +
a per-identity weakness breakdown + a **soft** note that it needn't roll every
build every cycle (sample, prioritize weak ones). It chooses where to spend
effort; the full outer eval is the backstop. No screen/subsample tier — the
**outer eval runs the full union** (per the user: ~1024 ep / ~40 min for
all-73 is acceptable).

## Selector syntax

The positional `objective` (and the TUI picker) resolve a token to a subset
`S`, in this order:

1. **exact catalog name** (`random`, `all`, `wiz-elf-cha-mal`) → unchanged
   single objective (`|S|=1`, or the existing `random`/`all`).
2. **bare role** in `ROLES` (`wiz`, `val`, …) → that role's identities
   (sugar for `<role>-*`). Roles are 3-letter and never collide with catalog
   names.
3. **comma list** (`wiz-elf-cha-mal,val-dwa-law-fem`) → exactly those
   identities (each validated against `IDENTITIES`).
4. **glob** (`*-elf-*-*`, `wiz-*`, `*-*-*-fem`) → identities matching the
   `fnmatch` pattern (covers race/align/gender groups).

A resolved generalist `S` gets a stable **objective name** for display /
run.json / local tagging: the role name for (2), the pattern for (4), else a
canonical `set:<n>:<sha1[:8]>` derived from the sorted member list. Empty or
unknown resolutions raise a clear error (generalizes `_unknown_objective`).

`|S| == 1` collapses to the existing single-identity path (full backward
compatibility). `random`/`all` keep their current meaning.

## Components (touch points)

Pure, unit-testable modules first; wiring second.

**1. Selector — `hub/selector.py` (new, pure).**
`resolve(token) -> Objective S` returning `(name, identities: tuple[str,...],
kind)` where `kind ∈ {single, random, all, set}`. Uses `objectives.ROLES`,
`IDENTITIES`, `fnmatch`. No I/O. Fully unit-tested (role expansion, glob,
list, dedup+sort, unknown → error, single/random/all passthrough).

**2. Union spec — `hub/objectives.py` + `harness/seeds.py`.**
`build_union_spec(identities, *, per_identity_size=15) -> ObjectiveSpec`:
`batch = tuple((seed, ident) for ident in sorted(S) for seed in range(k))`,
`kind="set"`, `aggregation="mean"`. `dev_spec(token)` resolves via the
selector: single/random/all → `CATALOG[...]` (unchanged); a set → the union
spec. `validation_spec(token, n, start=1000)` generalizes: held-out seeds
`start..start+n` **× each identity in S** (union validation), same
aggregation. `smoke` = 1 held-out seed × one member (contract check only).
Training seeds handed to the brief stay `range(k)` (0..14) — the wall holds.

**3. Aggregation — `harness/aggregate.py` (new, pure).**
From `Evidence.results` (each carries `.character`):
`per_identity_means(results) -> dict[identity, float]`,
`union_mean`, `floor()` (min identity + value), `coverage(S)`,
`regressions(parent_means, child_means) -> list[(identity, Δ)]`.
Union mean == `evidence.mean_progress` for a balanced union (asserted in a
test), so the gate keeps using `mean_progress`; these add the *reported*
per-identity view. Fully unit-tested.

**4. Brief — `harness/brief.py`.**
`build_brief` gains an `identities: list[str] | None` and
`per_identity: dict[identity, float] | None`. For a set: name the builds
(count + list), a compact **weakest-first** per-identity table from the
parent evidence, and the soft-sampling note ("~N builds; you needn't roll
every build each cycle — sample, prioritize the weak ones; full grading is
done for you"). Single identity → today's brief verbatim.

**5. Loop — `harness/loop.py`.**
- `dev`/`validation`/`smoke` from the generalized specs; `identities =
  sorted(set(dev.characters()))`.
- brief built with the per-identity parent means (grouped from
  `elite.dev_evidence`).
- gate unchanged in shape (union `mean_progress` up, then validation up).
- on a win: compute per-identity regressions vs parent → attach to the
  ledger row (reported), and **register per-identity slices** (§6).
- `_emit` carries the new per-identity state (parent means, candidate means
  once eval'd, floor, coverage) for the monitor.

**6. Registration — `harness/register.py`.**
`register_win_slices(hub, ..., evidence, identities, parent_digest)`: group
the union evidence by `.character`; for each identity in `S`, build an
`Evidence` whose `objective.seed_set = identity` and whose results are that
character's slice (== that identity's published batch), and `register` it.
Each slice passes the hub's `UnknownObjective`/`WrongBatch` checks unchanged —
one solution row, atoms across all of `S`. Single identity / `random` keep the
one-shot `register_win`. (Guard: only slice when `kind == "set"`.)

**7. Selection — `harness/select.py`.**
`select_parent` / `top_trusted_elite` accept a resolved objective. For a set:
fetch each member's per-identity elite pool, **intersect on `solution_digest`**
(coverage-gated: present in all of `S`), score each survivor by its union mean
over `S`, then reuse the existing trusted-filter → top-k → temperature
sampling (SELECT) / greedy max (migration). Empty intersection → seed
fallback. Client-side; no hub change. `_trusted` unchanged.

**8. CLI — `cli.py`.**
`evolve <selector>` resolves via §1; `board`/`elites` stay per-identity for
now (subset *boards* are the deferred "view" feature, not this). Update
`_unknown_objective` to mention roles / lists / globs. Keep `--json`, keep the
top-level traceback guard (house rule).

**9. TUI — the evolve window redesign.** See next section.

## TUI: the evolve window redesign

Two surfaces. Both verified by **rendering** (Textual → SVG → PNG → look),
per our standing rule — mount/content tests do not catch broken layout.

### Launch form (`tui/screens/evolve_form.py`)

Turn the single-pick objective list into a **multi-select set builder** (the
form already anticipated this):
- keep the type-to-filter Input;
- the option list becomes multi-select (space toggles; a role option at the
  top of a filtered group — e.g. typing `wiz` offers **"wiz — whole role
  (10)"** as the first option — selects the whole role at once);
- a live chip line under it shows the current set ("objective: **wiz** — 10
  builds" or "3 builds: …") and the resolved name;
- `_params()` passes the selector/`identities` through `EvolveParams`.
Single-pick still works (pick one identity or `random`).

### Live monitor (`tui/screens/monitor.py`, `status.py`, `run.py`)

The cockpit keeps PARENT / CANDIDATE / eval / lineage / ledger, and gains the
**per-identity scorecard** — the centerpiece of "floor first-class":

- **Scorecard panel** (new, in the Monitor tab above the episode tables): one
  row per identity in `S` — `build · bar · x̄ · Δ vs parent`, **sorted
  weakest-first**, the floor row marked. A header line shows `union x̄`,
  `floor`, and `coverage n/|S|`. Rendered by a new pure `status.scorecard(...)`
  from the run's per-identity means (derived in `run.py` by grouping the
  current batch's rows by `ep["character"]`, plus the parent means from
  state). For `|S|=1` the panel is suppressed (single-identity view is
  unchanged).
- **`#influences`** (today a placeholder): show the selected parent + its
  coverage over `S` ("influence #abcd · covers 10/10" or "seed · cold-start"),
  from the SELECT result threaded into state.
- **Ledger**: a registered row that regressed some build shows a small
  `⚠k` (k = #builds down) so a "won on average, lost on a build" outcome is
  visible without opening anything.
- Cockpit title shows the set compactly (`⚔ Evolution · wiz (10) · claude`).

State additions on the `_emit` dict (all additive, `run.py` back-compatible):
`identities`, `parent_means`, `candidate_means`, `floor`, `coverage`,
`influence` (digest + coverage). `RunMonitor.render_state` renders the
scorecard + influences when `identities` is present.

Design intent for the redesign: **beautiful, navigable, intuitive** — the
scorecard reads at a glance (weakest-first, floor marked, one bar per build),
navigation is the existing modal 2D scheme (the scorecard is a scroll target
like the episode tables), and nothing new competes for the cursor.

## Data flow (an evolve run over `S`)

1. `resolve(token)` → `S` (identities) + name.
2. SELECT: coverage-gated best-over-`S` trusted program, else seed
   (`--seed`, default AutoAscend). Its coverage → `#influences`.
3. cold-start: score the parent on union dev + union validation → per-identity
   means → floor/coverage.
4. per iteration: build the generalist brief (weakest-first breakdown + soft
   note) → mutate in the sandbox → smoke gate → union dev eval (gate: union
   mean up) → union validation eval (gate up) → on win: per-identity
   regressions to the ledger + **register per-identity slices** → the program's
   vector now covers `S`.
5. monitor renders the scorecard/floor/influences live throughout.

## Backward compatibility

- `|S| == 1` (a single identity) → union spec == the identity's spec; brief,
  register, select, and monitor all take their existing single-identity paths.
  Existing tests must stay green.
- `random`, `all` unchanged (`all` remains a hub-query marker; not evolvable).
- Additive `_emit` keys → old `run.py`/tests unaffected.
- No `CATALOG` change → no hub restart / DB wipe.

## Testing

- **Pure modules** (`selector`, `aggregate`, `objectives.build_union_spec`,
  `seeds` generalization, `brief`, `status.scorecard`): thorough unit tests,
  including `|S|=1` equivalence and the `union_mean == mean_progress` identity.
- **Loop**: a fake operator + fake evaluate → assert a set run registers **N
  slices** with correct per-identity batches and parents; regressions land in
  the ledger; single-identity path unchanged.
- **Select**: fake hub with partial-coverage entries → intersection is
  coverage-gated; empty → seed fallback.
- **TUI**: mount tests for the multi-select form and the scorecard presence,
  **plus** an actual render (SVG→PNG) of the monitor with a multi-identity run
  and of the form, eyeballed before "done."
- Full `pytest` (docker-gated tests skip locally as usual) green before the
  manual-test gate.

## Out of scope (deferred)

- **Smarter selection** — coverage-aware influence, verifier-trust weighting
  (**#12**).
- **Subset hub *boards*** (the "aggregated view" reading) — generalist boards
  as a first-class hub query; not needed to evolve.
- **ε-gated no-regression** — reported only for the MVP.
- **`all` reimplemented** as the clean per-identity subset query — nice
  consistency win, not required here.

## Manual-test gate

Per standing preference: implement on `m3/generalist-objectives`, run the
suite, verify the TUI by rendering — then **hold**. No push, no PR until the
user has driven manual testing and is satisfied.
