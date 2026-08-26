# Leaderboard rework: objectives as views on atoms

**Date:** 2026-08-26
**Branch:** `vkurenkov/website-leaderboard-all-identities`
**Status:** design approved (validated against an interactive prototype)
**Prototype:** https://claude.ai/code/artifact/a473d3fe-dc60-4125-b3b5-ad637124ba90

## Problem

The public hub website (`src/nethackers/hub/web/index.html`, served at `GET /`)
ships a leaderboard whose objective picker is hardcoded to three options —
`val-dwa-law-fem`, `random`, `all` (`index.html:526-530`). Three problems:

1. **You cannot pick across identities.** The board endpoint already accepts
   any of the 73 identities, but the UI exposes exactly one.
2. **`random` is a meaningless ranking view.** It is a frozen, natural-weighted
   *mixed* sample of episodes across many different characters
   (`objectives.py:_natural_character`). A program's "random score" depends on
   which characters it happened to draw — not a per-identity capability, not
   comparable across programs. `all` is a *breadth rollup* (`boards.py:112-115`)
   that rewards whoever ran more episodes, not who is actually good.
3. **AutoAscend is shown dishonestly.** The baseline row is appended to every
   board (`index.html:1086`) but always with the *global* `overall` (0.068) via
   `rebuildAutoascend` (`index.html:1200-1209`) — so the Valkyrie board shows
   0.068 instead of AutoAscend's actual `val-dwa-law-fem` score, even though
   `/baseline` already returns per-identity values.

## Goal

Reframe the leaderboard around **objectives = views on atoms**: a small set of
principled views (specialists + one generalist), an honest per-objective
AutoAscend floor everywhere, and a person's standing as the union of their
programs. No new stored state, no DB migration.

## Decisions (locked with the user)

- **Objective menu = Generalist + 13 roles + 73 identities.** Drop `random`
  and `all` from the picker. (Backend `random`/`all` objectives may remain for
  other consumers; they are simply unlisted.)
- **Aggregate boards (generalist, each role) show two columns: coverage (X/N)
  and mean**, ranked **coverage-first** (coverage desc, then mean desc, then a
  deterministic id tiebreak). A broad-shallow program outranks a narrow-deep
  one. The two columns are never collapsed into one number.
- **Per-component comparability is preserved.** A macro-average scores each
  member identity on *that identity's own published batch* (its
  `objective_digest`), exactly as `board()` does today — never by raw
  `identity` (which would mix in atoms from other objectives, e.g. `random`,
  that drew the same character on different seeds).
- **AutoAscend everywhere.** The floor row shows the *selected objective's*
  real AutoAscend score (identity → that identity; role → macro-average over
  the role's identities; generalist → `overall`). Every program/hacker row
  shows a **Δ vs AA** column. In the frontier heatmap, every cell no program
  has touched is painted with the dim AutoAscend floor value instead of a
  blank. AutoAscend is a pinned *reference* row, not ranked among contenders.
- **Hackers = union frontier, and it is the primary (default) view.** A person
  is the union of all their programs' best-per-identity, reported with the same
  coverage + mean + Δ columns, scoped to the objective picker. **No "firsts"
  column.**
- **Remove the two callout badges** ("deepest coverage / most cells held" and
  "first to light / firsts") from the website.
- **Fold in the `deepest` fix:** the per-identity board currently returns no
  `deepest`, so the "deepest reach" column renders `—` (`index.html:1293`).
  Compute it server-side.

## Non-goals / parked

- Verified tier (M2b) stays an empty state.
- The progress chart remains pinned to `val-dwa-law-fem` (`index.html:785`);
  not touched here.
- Backend `firsts_board`/`coverage_board`/`attainment` machinery stays as-is —
  only unsurfaced on the website. No behavior change there.
- A two-level role→identity picker is deferred; a single grouped `<select>`
  with three `<optgroup>`s is enough.
- Ascensions are zero across the game today; the `★` column renders `.`.

## Architecture

Two new **pure read views** over the atoms table (same spirit as
`views/boards.py` and `views/baseline.py` — nothing stored, no new tables), a
thin API extension, and a targeted frontend rework. No changes to the objective
catalog, so no `UNIQUE(name)` startup crash / DB wipe
(cf. the "Hub catalog change → DB wipe" hazard).

### Scope resolution

A small pure helper maps an objective token to an identity set:

```
resolve_scope(token) -> (kind, ids)
  "generalist"              -> ("generalist", all 73 IDENTITIES)
  token in ROLES ("val")    -> ("role", [id for id in IDENTITIES if id.startswith(token+"-")])
  token in IDENTITIES       -> ("identity", (token,))
  else                      -> raise / 404
```

Lives in `views/boards.py` (or a small `views/scope.py`) and is imported by the
API. It reuses `objectives.IDENTITIES` / `objectives.ROLES`. It is intentionally
distinct from `selector.resolve` (whose `kind="all"` and glob/comma handling
serve the evolve path, not the board's macro-average semantics).

### View: `aggregate_board(store, ids, tier)` — generalist + roles

For each identity in `ids`, pull that identity objective's atoms
(`store.iter_atoms(objective_digest=CATALOG[ident].digest(), tier=tier)`),
group by `solution_digest`, and compute a per-(solution, identity) mean
progression. Then roll up **per solution** across the identities it has atoms
on:

- `coverage` = number of identities in `ids` the solution has ≥1 atom on
- `total` = `len(ids)`
- `mean_progression` = mean of that solution's per-identity means over its
  covered identities
- `ascensions` = count of ascended atoms across the scope
- `deepest` = deepest milestone reached across the scope (ACHIEVEMENTS order)

Rank: `coverage` desc, then `mean_progression` desc, then `solution_digest`
asc. Assign `rank` 1..n. Returns `[]` when no atoms match. Rows:

```
{rank, solution_digest, owner, coverage, total, mean_progression, ascensions, deepest}
```

### View: `hacker_board(store, ids, tier)` — the Hackers tally

Same per-identity, per-`objective_digest` atom pull, but group by
`(owner, identity, solution)` → per-(owner, identity) take the **best** mean
across that owner's solutions → roll up per owner:

- `coverage` = identities in `ids` the owner has touched (via any of their
  solutions)
- `mean_progression` = mean of the owner's best-per-identity over covered
  identities

Rank: `coverage` desc, then `mean_progression` desc, then `owner` asc. Rows:

```
{rank, owner, coverage, total, mean_progression}
```

New file `views/hackers.py`. No `firsts`.

### `board()` gains `deepest`

`views/boards.py::board()` adds a `deepest` field per row: the deepest
milestone (ACHIEVEMENTS order) across that solution's atoms on the objective,
or `None`. Reuse a `_deepest(milestones)` helper (mirror of
`views/baseline.py:_deepest`); factor to one shared helper to avoid drift.
Existing fields and ordering are unchanged.

### API (`api.py`)

- `GET /board?objective=<token>&tier=`:
  - `token` an identity → `board()` (now includes `deepest`).
  - `token == "generalist"` or a role → `aggregate_board(resolve_scope(token).ids, tier)`.
  - unknown token → 404.
- `GET /hackers?objective=<token>&tier=` (new) → `hacker_board(resolve_scope(token).ids, tier)`.
  `objective` defaults to `generalist`.
- `GET /baseline` unchanged (already returns `per_identity` + `overall`).

The `metric=coverage|firsts` branch of `/board` is untouched (still used by
nothing on the new site, retained for API back-compat).

### Frontend (`web/index.html`)

Targeted rework of the leaderboard + frontier + people logic; the masthead,
sidebar, glossary/refs, progress chart, contribute/FAQ, popup modal, and audio
viz are untouched. Concretely:

- **Picker:** replace the three hardcoded `<option>`s with three `<optgroup>`s
  (Generalist / Roles / Identities) built in JS from the existing
  `ROLE_NAMES`/`ROLE_VARS` tables (`index.html:762-778`). Default `generalist`.
- **Tally toggle:** order **Hackers, Programs**; Hackers `aria-pressed="true"`.
  `curView` defaults to `"people"`.
- **Two board shapes** in `renderBoard`, branching on scope kind:
  - identity → `# | program | hacker | deepest reach | score | Δ vs AA | ★`
  - generalist/role → `# | program | hacker | coverage | mean | Δ vs AA | ★`
  - hackers mirror these minus the program column and `★`.
- **Δ vs AA** per row = `row.mean − AA_scope_mean`, green/red/grey.
- **Per-objective AA floor row** via a reworked `rebuildAutoascend`: identity →
  `BASELINE.per_identity[id]`; role → macro-average over the role's ids;
  generalist → `BASELINE.overall`. Pinned reference row, `Δ` = `—`.
- **Frontier floor painting** in `renderFrontier`: a cell with no program value
  falls back to `BASELINE.per_identity[id]` rendered dim/italic ("floor"),
  overwritten by a program value when one lands. Champion regime sources from
  the **generalist** board #1 (was `random`, `index.html:1250`).
- **Remove** the `.callouts` markup and `loadCallouts`; remove all `firsts`
  references. Drop `random`/`all` from `OBJ_CAP`/`setCap`.
- New `loadHackers()` fetches `/hackers?objective=&tier=`.

The prototype (link above) is the reference implementation for all of this
frontend logic; it was verified headless (jsdom, no errors) and by screenshot.

## Data contracts (response shapes)

```
GET /board?objective=val-dwa-law-fem&tier=self-reported
[ {rank, solution_digest, owner, episodes, ascensions,
   median_progression, mean_progression, deepest}, ... ]

GET /board?objective=generalist&tier=self-reported          # also ?objective=val (role)
[ {rank, solution_digest, owner, coverage, total,
   mean_progression, ascensions, deepest}, ... ]

GET /hackers?objective=generalist&tier=self-reported
[ {rank, owner, coverage, total, mean_progression}, ... ]

GET /baseline        # unchanged
{owner, per_identity: {id: {progression, deepest, episodes}}, overall}
```

## Testing

- `tests/hub/test_boards.py`: extend for `aggregate_board` — coverage-first
  ordering (a broad-shallow solution outranks a narrow-deep one), per-component
  comparability (atoms under a *different* objective on the same character do
  not leak in), coverage/mean math, `deepest`, empty state; and `deepest` on
  `board()`.
- `tests/hub/test_hackers.py` (new): union-frontier per owner — best-per-
  identity across a person's multiple solutions, coverage/mean, ordering, empty.
- `tests/hub/test_api.py`: `/board?objective=generalist|<role>` shapes + 404 on
  junk; `/hackers` shape and default; `deepest` present on the identity board.
- Frontend: headless verification pass (jsdom smoke — picker option count,
  both board shapes render, no console errors — plus a qlmanage screenshot),
  mirroring how the prototype was verified.

## Files

- `src/nethackers/hub/views/boards.py` — `aggregate_board`, `deepest` on `board`, `resolve_scope`
- `src/nethackers/hub/views/hackers.py` — new `hacker_board`
- `src/nethackers/hub/api.py` — `/board` token dispatch, new `/hackers`
- `src/nethackers/hub/web/index.html` — leaderboard + frontier + hackers rework
- `tests/hub/test_boards.py`, `tests/hub/test_hackers.py`, `tests/hub/test_api.py`
