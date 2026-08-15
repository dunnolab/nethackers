# Frontier View — Two Regimes over a Numbered Grid (Design)

**Status:** approved (2026-08-15), building.
**Supersedes:** the `MapView` / `frontier` portion of
`2026-08-15-nethackers-tui-redesign-design.md` (which rendered
`render_attainment` — an identity×milestone coverage table). This replaces
that with a role×variation grid of progression **numbers** plus two regimes.

## Goal

Turn the **Frontier** view (TUI `MapView` + CLI `frontier`) into a single
shared grid — a cell per role (full names), every identity-variation
labeled, each showing a progression **number** (0–1) — with **two subtabs**
that change what the numbers mean.

## The metric (the crux)

Every number is a **mean over seeds**, aggregated as a **mean of means**:

- **Per-identity cell value** = mean progression over that identity's
  evaluated seeds/episodes (e.g. an identity with 15 seeds → the mean of
  those 15 progressions). This is exactly what `AVG(progression) … GROUP BY
  identity` yields, and what an elite/board `score`/`mean_progression`
  already is.
- **Aggregate (role header, overall)** = the mean across a *subset of
  identities* of those per-identity means. Aggregates are the **mean of the
  cell values** over the evaluated identities in that subset (skip
  unevaluated `—` cells). Role header shows the role subset's mean; the
  panel shows the overall mean across all evaluated identities.
  Aggregates are **means, never max/best.**

Tint on a number is a scan aid only (dim→green→amber ramp, gold ★ at
ascension ≥ 0.8746); the value is the number. Keep the tint (a later change
can drop it).

## Two regimes (subtabs)

Both feed the **same renderer** a `dict[identity, float | None]` (None →
unevaluated → rendered `—`).

### ◆ Universe — all programs
Each cell = the **best** program's per-identity mean on that identity (any
program may fill any cell). Source: `client.elites("all")`, take the
**rank-1** row per identity → `{identity: score}`. Already exists; no hub
change. Note line: "each number = the best program's mean on that identity."

### ◇ Program — one solution
No picker. Always **THE best program available** — "the champion" — shown
across all identities. Champion = `client.board("random")[0]`
(the leaderboard #1: `solution_digest`, `owner`). Its per-identity means come
from the **new endpoint** (below). Cells the champion never ran → `—`. Note
line names the champion: `@owner/short-digest`.

Champion selection rationale: the Leaderboard tab already shows
`board("random")`, so "the best program" == that board's #1. If
`board("random")` is empty, the regime shows an empty state ("no ranked
programs yet"); do not fall back silently.

## New hub endpoint

`GET /solutions/{digest}/frontier` → `list[dict]`, one row per identity the
solution has atoms for:

```json
[{"identity": "wiz-elf-cha-mal", "progression": 0.61, "episodes": 15}, ...]
```

- View `read_solution_frontier(store, solution_digest)` in
  `hub/views/attainment.py` (or a small new `hub/views/solution.py`):
  `SELECT identity, AVG(progression) AS progression, COUNT(*) AS episodes
  FROM atoms WHERE solution_digest = ? GROUP BY identity` (via `iter_atoms`
  or direct SQL, matching the file's existing style).
- Endpoint validates the digest exists (`store.get_solution`); 404 with
  `unknown solution digest: {digest!r}` if not — mirroring `get_solution`.
  A known solution with no atoms returns `[]`.
- `progression` is a float; `episodes` an int.

## Client

`HubClient.solution_frontier(digest: str) -> list[dict[str, Any]]` →
`self._get(f"/solutions/{digest}/frontier")`. Bare list, like the other
read methods.

## Shared renderer

`render_frontier_grid(scores: dict[str, float | None], *, note: str) ->
RenderableType` in `hubclient/render.py` (beside `render_attainment` /
`render_elites`):

- Groups the 73 identities by role using
  `CATALOG`-derived identity keys; full role names
  (`arc→Archeologist … wiz→Wizard`, a module-level map of all 13).
- A `rich.table.Table(box=SQUARE, expand=True)` with **4 equal columns**;
  roles flow down the columns in canonical order.
- Each role cell: bold-amber full name + `  mean X.XX` (the role subset's
  mean of cell values, dim; `—` if none), then one line per variation:
  `{race-align-gender:<12} {value:>4.2f}` (value tinted; `—` when None).
- Variation label = the identity minus its role prefix
  (`wiz-elf-cha-mal → elf-cha-mal`) — shorthand, fits 4 columns.
- Overall mean across evaluated cells is carried in `note` by the caller,
  or appended; keep the renderer pure (no hub calls).

Data-assembly helpers live in a new `hubclient/frontier.py` (imported by
both TUI and CLI so the logic is not duplicated):

- `universe_scores(client) -> dict[str, float]` — rank-1 elites spread.
- `champion(client) -> tuple[str, str] | None` — `(digest, owner)` or None.
- `champion_scores(client, digest) -> dict[str, float]` — from
  `solution_frontier`.
- `overall_mean(scores) -> float | None` — mean of non-None values.

## TUI — `MapView` becomes subtabbed

Frontier panel gains a secondary tab row (Universe / Program) under the
panel title. Textual `Tabs` (or `TabbedContent`) styled to the dungeon
theme (active = amber; inactive = dim), NOT the blue default. Switching a
subtab re-renders the body:

- Universe → `render_frontier_grid(universe_scores(client), note=…)`.
- Program → resolve `champion(client)`; empty state if None; else
  `render_frontier_grid(champion_scores(client, digest), note="@owner/dig…")`.

Keep the existing `_HubView` outage handling (a friendly "could not load"
line, never a traceback). Both subtabs fetch on show.

## CLI parity

`nethackers frontier` (aliases `map`, `attainment` retained):

- default → Universe grid.
- `--program [DIGEST]` → Program regime. `DIGEST` optional (`nargs="?"`);
  omitted → the champion; given → that specific solution's frontier.
- `--json` → the underlying `{identity: value}` map (+ regime, + champion
  digest/owner when applicable), never the rich table. No traceback on hub
  errors (existing top-level guard).

## Testing

- Hub: `read_solution_frontier` (per-identity AVG, episode count, empty,
  unknown-digest 404) — an API test through the FastAPI test client.
- Client: `solution_frontier` hits the right path, returns the list.
- `frontier.py`: universe rank-1 selection; champion from board[0]; None
  when board empty; `overall_mean` skips None.
- Renderer: role grouping + full names, `mean` header aggregate, `—` for
  None, tint thresholds, 4 columns. Assert on structure/among-strings, not
  a full-frame snapshot.
- TUI: `MapView` renders both subtabs (mock client); subtab switch changes
  the body; hub outage → friendly line. Verify the built view **renders**
  (screenshot loop), not just mounts.
- CLI: `frontier` default vs `--program`; `--json` shape; hub-down guard.

## Out of scope / decided

- **No program picker** (Program = champion only; CLI `--program DIGEST`
  is the only override).
- Keep the **tint**; keep **shorthand** variation labels.
- `render_attainment` becomes unused by MapView/CLI — remove it and its
  tests if nothing else references it (Home uses its own `attainment_panel`,
  not `render_attainment`).
- Multi-select / multi-objective remains unwired (unchanged).
