# Public website (nethackers.dunnolab.ai) — Design

**Status:** approved design (2026-08-25), ready for implementation plan.
**Prototype (validated with the user over many rounds):**
https://claude.ai/code/artifact/d83b73e9-f42c-40c6-a70f-13bcec5df63b
This is a working, illustrative HTML page. It is the source of truth for the
visual design, IA, and copy. This spec covers turning it into the production
site served by the hub on live data.

## Goal (v1)

Ship the public site at `nethackers.dunnolab.ai`. It explains what the project
is and why to care, shows the live standings (leaderboard, frontier,
progress-from-AutoAscend), and routes two audiences (AI researchers, AI
enthusiasts) to the two contribution paths. First version — we will iterate;
keep scope tight.

Today the hub is a pure JSON API behind Caddy (`nethackers.dunnolab.ai ->
hub:8000`); there is no frontend. This adds one.

## The validated design (from the prototype — do not redesign)

- **Aesthetic:** a 1998-style research-group homepage. Fonts: **Times New
  Roman** (prose) + **Courier New** (all data/ASCII/terminal), with
  metric-identical webfont fallbacks **Tinos**/**Cousine** loaded from Google
  Fonts so devices lacking the MS fonts (Android/Linux) still render the look.
  Palette: dunnolab indigo `#2f3677` + red `#FF0000` alert on near-white.
  Signature devices: ASCII masthead + scrolling `<marquee>` message line;
  sidebar `CONTENTS` index; LCD hit-counter odometers; bordered tables with
  Win95 bevel buttons; a `bgcolor`-tinted frontier heatmap; an **ASCII bar
  chart** for progress; the RIP-tombstone footer; a faint ambient NetHack demo
  (`@` pacing a room) behind the wordmark.
- **IA — one scrolling page, six anchored sections** (sidebar index):
  `[1] What is this?` · `[2] Why you should care` · `[3] Leaderboard` ·
  `[4] The Frontier` · `[5] Progress` · `[6] Contribute`. Program drill-down is
  a modal (a faux popup window). Per-identity drill-down pages are **out of
  scope for v1**.
- **Naming:** a bot is a **program**; its producer is a **Hacker** (Net +
  Hackers). Leaderboard toggles `Programs | Hackers`.
- **Honesty is first-class:** every board carries `self-reported*`; a global
  **Self-reported | Verified** tier toggle drives every data table, and
  **Verified is honestly empty** until M2b verification exists. **AutoAscend
  sits at the bottom of every table as the baseline** (the starting point / a
  yardstick), in both tiers.
- **Glossary/reference popovers:** dotted-underlined terms open a Web-1.0
  "definition window" on hover/focus/tap — a `REFS` registry (~40 entries) of
  real citations (arXiv/GitHub/tweet links, incl. Rocktäschel's tweet), NetHack
  jargon (nethackwiki links), and the 13 roles (ASCII emblem + wiki link).
- **Copy:** grounded in `paper/`; no AI slop (user is very slop-sensitive —
  see the memory note). Framing: solving NetHack is an **open question**, an
  open attempt to answer **together**. The **paper link is a `#` placeholder**
  until the paper is public.

## Architecture — how it's served

Keep the JSON API exactly as-is (the CLI depends on it). Add a thin web layer
to the same FastAPI app:

- **Serve the page client-side-rendered (CSR), not SSR.** The prototype is
  already a self-contained HTML/CSS/JS page whose render functions
  (`renderBoard`, `renderFrontier`, `renderChart`, `openProgram`, the glossary,
  the demo, the tier/objective/view toggles) take plain data objects. Porting
  all of that to server-side templates would be a large rewrite for no v1
  benefit; instead **swap the hardcoded data constants for `fetch()` calls to
  the existing JSON endpoints.** All prose, design, glossary, and interactions
  are static and ship as-is.
- **New hub surface:**
  - `GET /` → returns the page HTML (`HTMLResponse`, or serve
    `web/index.html`). The Google-Fonts `<link>` stays (Tinos/Cousine).
  - `GET /site/*` (or `/static/*`) → the CSS/JS assets if split out; simplest
    v1 is a single self-contained `index.html` (as the prototype is) so no
    static mount is strictly required.
- **No new infra.** Caddy already proxies `/` to `hub:8000`; the page ships in
  the hub image. CSP: the page only needs Google Fonts (already allowed in the
  artifact; the deploy Caddyfile does not currently set a restrictive CSP, so
  fonts load fine).

## Data flow — endpoint mapping (mostly existing)

| Page element | Source |
|---|---|
| Objective dropdown | `GET /objectives` |
| Leaderboard, Programs view | `GET /board?objective={obj}&tier={tier}` (`random`=generalist, `all`=breadth, or an identity) |
| Leaderboard, Hackers view | client-side aggregate over `/board` + `/search` (best score, program count, ascensions per owner) — no new endpoint |
| Coverage / firsts callouts | `GET /board?metric=coverage` / `?metric=firsts` |
| Frontier — Universe | `GET /elites?objective=all` → rank-1 per identity |
| Frontier — Program (champion) | `GET /board?objective=random`\[0] → `GET /solutions/{digest}/frontier` |
| Program drill-down modal | `GET /solutions/{digest}` + `/solutions/{digest}/frontier` |
| Verified tier (any table) | same endpoints with `tier=verified` → returns `[]` until M2b (drives the honest empty state) |
| **Counters** (programs, hackers) | **new `GET /stats`** (or derive: programs = `/search` count; hackers = distinct owners) |
| **Progress chart** (best-so-far time series) | **new `GET /progress`** |
| AutoAscend baseline (row on every table + progress start line) | **new `GET /baseline`**, backed by **computed AutoAscend atoms**. AutoAscend is the platform's reference floor, not a participant submission (it must not compete for rank or be owned by a Hacker), so it gets its own endpoint rather than a `solutions` row. Its atoms do not exist yet — they must be computed (see below). |

### New endpoints (the only server work)

1. **`GET /progress`** → day-bucketed best-so-far series, computed by replaying
   `atoms.created_at`:
   ```json
   {
     "baseline": {"label": "AutoAscend", "frontier": 0.089},
     "series": [
       {"t": "2026-08-23", "frontier": 0.124, "ascensions": 0, "coverage": 28},
       ...
     ]
   }
   ```
   - `frontier` = best-so-far mean progression on the headline objective
     (`val-dwa-law-fem` today; generalize later); `ascensions` = cumulative;
     `coverage` = attainment cells lit best-so-far. New view
     `hub/views/progress.py` (pure read over `atoms` + `attainment`), API route,
     `HubClient.progress()`. Tier-aware (`?tier=`).
2. **`GET /stats`** → headline counters:
   ```json
   {"programs": 3, "hackers": 1, "ascensions": 0, "identities_touched": 1, "best": 0.167}
   ```
   Small SELECTs over `solutions`/`atoms`. (Alternatively fold counters into an
   existing endpoint; a dedicated `/stats` is cleanest for the sidebar.)
3. **`GET /baseline`** (+ **a compute step**) → AutoAscend's reference scores,
   the shape the tables need to draw the baseline row/line:
   ```json
   {"owner": "autoascend", "per_identity": {"val-dwa-law-fem": {"progression": 0.089, "deepest": "Dlvl 3", "episodes": 15}, ...},
    "overall": 0.089}
   ```
   - **Compute step (prerequisite):** run AutoAscend as the `ArenaBot` through
     the arena on each catalog objective's published `(seed, character)` batch
     to produce baseline atoms (mean progression, ascensions, deepest milestone
     per identity). This reuses the existing arena/eval path (the harness
     already cold-starts from AutoAscend and scores it). Store the atoms
     distinctly from participant `atoms` — a `baseline_atoms` table (or `atoms`
     with an `owner='autoascend'` marker kept out of the boards/elites). Compute
     once (re-run if the arena/objectives change); it is cheap (~14,400 steps/s;
     ~1,100 episodes for the 73 identities × 15).
   - **Scope:** v1 can compute the baseline for the active/headline objectives
     first and expand to all 73; `/baseline` serves whatever has been computed,
     and a table shows no baseline row for an objective with no baseline atoms
     yet.
   - New view `hub/views/baseline.py`, API route, `HubClient.baseline()`.

Everything else reads endpoints that already exist.

## Error / empty / loading states (already designed in the prototype)

- **Hub unreachable / endpoint error:** a friendly one-line message per panel,
  never a traceback (mirror the CLI/TUI `_HubView` behaviour).
- **Empty boards** (e.g. generalist has no runs): the "be the first to register
  one" state; the AutoAscend baseline row still shows.
- **Verified tier:** empty everywhere with the "verification (M2b) isn't live"
  note; baseline still shows.
- **Loading:** show the static page immediately (prose/design), populate data
  panels on fetch; a brief placeholder while fetching.
- **localStorage** (hit counter, theme is dropped — single light theme): wrap
  in try/catch; the artifact runs sandboxed.

## Testing

- **New views/endpoints:** unit tests for `progress` bucketing (best-so-far
  monotonicity, cumulative ascensions, coverage, empty, tier) and `stats`
  counts; API tests through the FastAPI test client (as `tests/hub/test_api.py`
  does).
- **`GET /`:** returns 200 HTML containing the masthead + section anchors.
- **Client wiring:** headless (jsdom) — each render function populates from a
  mocked fetch; tier/objective/view toggles re-fetch/re-render; glossary refs
  all resolve; empty/verified states render. (The prototype already has a jsdom
  harness pattern.)
- **Visual:** render to PNG (the qlmanage / script-stripped-serialize approach
  used throughout the prototype) and eyeball, since the demo's `setInterval`
  blocks a naive headless idle.
- **Baseline:** unit test for the baseline view (computed AutoAscend atoms →
  per-identity scores, deepest, empty when uncomputed); API test that
  `/baseline` returns them; a check that the site draws the baseline row/line on
  the leaderboard, frontier, and progress tables. The compute step itself
  (running AutoAscend through the arena) is verified like other eval paths.

## Deploy

Ships in the hub image; Caddy already routes the domain. Reuse the M1 deploy
recipe (see the `m1-remote-hub-login-shipped` memory: `cap_drop`/`chown`
gotcha, `docker compose` on the hub VM `45.91.237.200`). No new services.

## Out of scope (v1) / parked

- Per-identity drill-down pages (`/i/{identity}`); the program modal is enough.
- Server-side rendering; multi-objective progress; a live cross-viewer visitor
  counter (the odometer is per-viewer localStorage + a base number).
- The **paper link** (placeholder `#` until the paper is public).
- Generalizing `/progress` beyond the single headline objective.

## Decisions taken

- **CSR + fetch**, not SSR (reuse the prototype directly). *(default, accepted)*
- **AutoAscend baseline = a dedicated `GET /baseline` endpoint backed by
  computed AutoAscend atoms** (a compute step runs AutoAscend through the
  arena). NOT a registered `solutions` row and NOT a hardcoded constant —
  AutoAscend is the reference floor, must not compete for rank, and its atoms
  have to be computed. *(user decision, 2026-08-25)*
- **Hackers view = client-side aggregate**, no new person endpoint.
- Three new read endpoints: `/progress`, `/stats`, `/baseline` — plus the
  AutoAscend-atoms **compute step** that `/baseline` depends on.

## Decisions still needed from the user

1. **Paper link target** — arXiv, a PDF in the repo, or leave `#` for v1?
   *(default: `#` placeholder until the paper is public.)*
2. **Baseline compute scope for v1** — compute AutoAscend atoms for all 73
   identities up front, or just the active/headline objective(s) first and
   expand? *(default: headline first, expand.)*
