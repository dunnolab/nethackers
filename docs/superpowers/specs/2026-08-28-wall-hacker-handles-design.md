# Dungeon-wall hacker handles — `@username` runners (design)

**Status:** validated by live iteration with the user (2026-08-28); ready for a
productionization plan. This is a **decision record**, not a design exploration —
the look and feel were approved by tuning a working prototype in the browser
(sliders), not on paper.

**Spec relationship:** additive to the audio-reactive dungeon wall
(`2026-08-25-dictionary-audio-viz-design.md`) and the public website
(`2026-08-25-public-website-design.md`); lands on the same page, `web/index.html`.

## What it is

The dungeon wall around the `.page` frame previously patrolled a **single
anonymous gold `@` hero**. This replaces it with **up to 20 `@username` runners —
the handles of hackers registered with us** — crawling the whole-page perimeter of
the wall. It turns an ambient flourish into a live, honest showcase of the people
in the contest: as you scroll and as the music plays, real registered handles
wander the descent.

Everything else about the wall (the audio-reactive descent, depth ladder, palette,
volume-as-tranquility, the control bar) is unchanged.

## Validated design (do not redesign — this is what the user approved)

Arrived at by iterating a working prototype, ending on the `?tune=1` slider panel.

- **On the wall, riding the whole page.** Runners ride the **full-page perimeter**
  (`per`), not just the visible slice. Each maps to a fixed point *on the page*, so
  it scrolls naturally with the content and you meet different handles as you
  scroll — it crawls the whole landing page, never sticking to the viewport.
  - This also **fixed the original scroll-lag**: the lone `@` indexed the
    *visible-only* cell list (`vis`), which resizes every scroll frame and made the
    glyph jump. Indexing the stable full perimeter removes the jump.
- **Count: up to 20** (`MAX_RUNNERS = 20`); the live count is
  `min(registered hackers, 20)`.
- **Independent, calm motion — never a pattern, never a pile-up.**
  - **Stratified start positions** (evenly spread around the perimeter, small
    jitter) so a few are *always* on screen — no empty frames.
  - Each runner has its **own distinct crawl speed** (evenly spread, no two alike),
    so they drift past one another: no fixed rotating pattern, no permanent
    clumping. Position is a normalized fraction `pos ∈ [0,1)`.
  - **Darts are driven by the music** (the `energy` that tracks volume) — calm
    steady crawl at rest, gentle lunges on the beat, riding the local spectrum
    spike outward and settling back.
- **Look — "cleared floor".** Each handle is drawn as **grid-aligned terminal
  glyphs** (one character per its own tight cell) at a size **smaller than the wall
  glyphs**. When the music blooms, an **opaque parchment patch fades in behind the
  handle** — the descent "clears" for it — with **dark ink text** on top, so it
  reads like a name written on revealed dungeon floor. At rest (no bloom) there is
  no patch; the muted ink text reads fine on the light wall.
  - Rejected alternatives (prototyped and dismissed by the user): a dark
    semi-transparent "chip" plate (reads as modern UI, not terminal); heavy-outline,
    glow, and reverse-video finishes; and colored text pickers.
- **Orientation.** The `@` kisses the wall and the name trails outward into the
  margin: **`@username`** on the right / top / bottom walls, **`username@`** on the
  left wall (mirrored so the `@` still meets the wall).
- **Tuned constants (the approved feel):**

  | Parameter | Value | Meaning |
  |---|---|---|
  | font size | `ch × 0.75` | glyph scale vs the wall glyphs |
  | runners | 20 | `min(registered, 20)` |
  | crawl speed | 0.8 | drift pace at rest |
  | dart / music kick | 0.3 | how hard they lunge on the beat |
  | spike reach | 0.6 | how far they ride outward on a beat |

- **Frame stays aligned; reduced-motion respected.** The wall still frames the
  `.page` box corners (a mid-iteration viewport-clamp that detached the top border
  was reverted). Under `prefers-reduced-motion` the idle crawl is suppressed, as
  the wall already does.

## Architecture

- Client changes are confined to the **`#dictviz` IIFE in `web/index.html`** — the
  existing wall canvas — plus **one small new hub endpoint** (below).
- A `RUNNERS` array (one object per handle: `name`, normalized `pos`, velocity,
  ride, and a distinct `spd`). The draw loop advances each independently, indexes
  the full perimeter, culls off-screen, and renders the cleared-floor label.
- **New endpoint — `GET /hackers/random?n=<N≤20>`** → a JSON list of up to N random
  **distinct hacker handles**, sampled **server-side**:
  `SELECT DISTINCT owner FROM atoms … ORDER BY RANDOM() LIMIT n`. It reads the SAME
  table as the leaderboard's `hacker_board` (real *scored* submissions), so it stays
  honest with **no allowlist**: the AutoAscend baseline lives in the isolated
  `baseline_atoms` table, and any seed/root that sits only in `solutions` (never
  scored) can't appear — only real hackers do. Far cheaper than `GET /hackers`,
  which additionally aggregates coverage + mean and returns all rows; we only need a
  handful of names. Lives in `api.py` (route) + a small `Store` method.
- **Data sourcing (real implementation):** the dictviz IIFE does a **one-time
  `GET /hackers/random?n=20`** on init and assigns the handles to `RUNNERS`.
  **Honest by construction:** only real handles; if fewer than 20 are registered,
  show that many — never a fabricated name. Degrades gracefully — an empty/short
  list or a failed fetch just yields fewer (or no) runners; the wall still runs.

## Productionization scope (cleanup + tests, NOT a rebuild)

The look and feel are done and live in the working tree, driven by `STUB_HANDLES`
and dev query hooks. The plan turns that prototype into a production feature:

- **Add `GET /hackers/random`** — an `api.py` route + a small `Store` method
  (distinct `owner`s from `atoms` — the leaderboard's source, so baseline/roots are
  excluded structurally — random-ordered, `LIMIT n` clamped to ≤20) — and **replace
  `STUB_HANDLES`** with a one-time fetch of it; degrade gracefully (empty / short /
  failed → fewer or no runners) without errors.
- **Strip the dev-only hooks** from the shipped page, leaving the cleared-floor
  treatment and the tuned values as baked literals:
  - `?tune=1` slider panel, `?hc` (color picker), `?ht` (treatment picker),
    `?runners` (count override), and the `STUB_HANDLES` list.
  - Keep the existing `?vizpreview` synthetic-spectrum hook only as already scoped
    by the dictionary-audio-viz spec (dev harness), not new to this work.
- **Tests** (the seams; a canvas + Web-Audio loop is not unit-testable):
  - **`GET /hackers/random?n=20`** (new, `TestClient` in `tests/hub/test_api.py`):
    returns ≤20 **distinct** scored hacker owners, **excludes** a seed/root that lives
    only in `solutions` (unscored), respects/clamps `n`, and returns `[]` when no
    scored hackers exist.
  - `GET /` still serves the page and the viz markers; the jsdom
    `tests/hub/web/wire.test.mjs` stays green (canvas is stubbed there, so it
    guards the data layer — stub `/hackers/random` and ensure the dictviz read
    doesn't throw and degrades on rejection).
  - Extend the **Playwright dev harness** (`scripts/dict_viz_preview.mjs`) to
    screenshot the handles idle / loud / scrolled and confirm they render and stay
    spread across scroll positions. Dev-only, **not wired into CI** (CI is
    pytest-only) — called out so it isn't mistaken for coverage.
- **Confirm** perf (still one pass over all perimeter cells, only visible rows
  drawn; ≤20 handles), reduced-motion, and both light/dark themes.

## Out of scope / parked

- Linking a handle to its hacker's page / leaderboard row (hover, click) — the
  wall is `pointer-events:none` ambient decoration.
- Per-viewer handle selection or persistence; showing more than 20; de-duplicating
  against the corner `#demo` game.
- Any change to the audio wall itself, its palette, depth ladder, or control bar.
