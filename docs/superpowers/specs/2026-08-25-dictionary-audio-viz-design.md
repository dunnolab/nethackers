# The Nethack Dictionary — audio-reactive dungeon border (design)

**Status:** validated by live iteration with the user (2026-08-25); ready for a
productionization plan. This is a **decision record**, not a design exploration —
the look was approved by iterating on a working prototype, not on paper.

**Spec relationship:** additive to the public-website work
(`2026-08-25-public-website-design.md`); lands on the same branch/PR.

## What it is

An opt-in, **starts-silent** background track — *"The Nethack Dictionary"*, used
with the artist's permission — on the public site, rendered as an **audio-reactive
ASCII dungeon wall around the `.page` frame**. The music's spectrum carves the
frame's borders into a NetHack descent: deeper = louder = more colourful monsters,
red dragons/liches and the Amulet at the deepest, a gold `@` patrolling. **Volume
controls the whole page's tranquility** — silent is a calm, near-static frame you
read straight past; louder brings the dungeon alive.

## Validated design (do not redesign — this is what the user approved)

- **Border-frame, not full background.** The effect wraps `.page`'s perimeter
  (mainly the left/right walls while scrolling the long page); the content column
  stays clean and readable.
- **Depth ladder: spike height = dungeon depth.** A ~46-glyph NetHack ladder
  (surface `.` → Mines `k o G g h` → mid `@ Z n & T` → deep `V L D H P` → `_` altar
  → Amulet `"` → Astral `*` → ascend `<`). The glyph **varies within a depth band
  by cell + time** so it reads as a living descent, not a fixed ladder. Depth is
  **sized to the visible margin** so the full descent — including the red monsters
  and the Amulet at the bottom — lands on-screen instead of clipping off.
- **Colour: a per-glyph NetHack palette** (green / brown / blue / magenta / orange
  / red / gold / gray / cyan / white), **theme-aware** for light and dark. Colour
  deepens with the descent — shallow is muted, deep is vivid.
- **The `@` hero:** gold, always visible (a ground-coloured halo keeps it legible
  over any glyph); it **rides the local spike** (lifted out on loud/deep segments,
  dropped back to the wall when it quiets), moves **sporadically** (random darts +
  friction, not a steady march), and its **speed scales with volume — frozen at
  silence**.
- **Volume = tranquility.** Spikes, motion, glyph-shuffle, and `@` speed all scale
  with volume. At 0 it is a faint, near-static frame outline — fully readable.
- **Opt-in + starts silent.** Nothing plays until the user raises the volume, which
  is also the browser gesture that unlocks audio — no jump-scare.
- **Stable perimeter (no scroll flicker).** The border is one structure sized to
  the page, not rebuilt-clipped to the viewport, so scrolling slides it smoothly
  (the earlier viewport-clipped rebuild reset the smoothing every frame → flicker).
- **Jagged, gap-toothed**, not a solid slab (per-cell response variation).
- **Volume bar:** a fixed dark console strip (matching the site's `--screen`
  chrome), **centred**: play/pause, a gold ASCII meter, `%`, and the note
  *"something is climbing up out of the dark ♫"* with a slow surfacing pulse (the
  emerging-from-the-deep feeling). No "now playing" text.
- **Reduced-motion respected; theme-aware** (light + dark palettes).

## Architecture

- Everything lives in the hub's self-contained `web/index.html`: a fixed
  full-viewport `<canvas id="dictviz">` behind `.page` (z-index 0), an IIFE that
  reads `.page`'s rect each frame to build the wall, and the `#dictbar` control.
- Audio graph: `new Audio("/dictionary.mp3")` → `MediaElementSource` → `Analyser`
  (fftSize 2048) → `Gain` (starts 0) → destination. Same-origin file so the
  analyser can read the samples (a cross-origin/YouTube embed cannot be analysed).
- New hub route **`GET /dictionary.mp3`** → `FileResponse(web/dictionary.mp3,
  audio/mpeg)`, **404 when the file is absent**.

## Production decisions (user, 2026-08-25)

1. **Audio asset — kept OUT of git, shipped via the image/deploy** (not committed;
   the 19 MB file would bloat git history + the PyPI wheel forever). The system
   **degrades gracefully without it**: the route 404s, `audio.play()` fails
   silently, and the viz still runs (idle/static) — so **dev, CI, and fresh clones
   work without the 19 MB file**. The exact image-inclusion mechanism (Dockerfile
   step vs deploy-time placement) is a plan task.
2. **Lands on the same branch/PR as the website**
   (`vkurenkov/brainstorm-website-leaderboard-design`).
3. **Executed via spec → plan → subagent-driven-development.**

## Productionization scope (this is cleanup + tests, NOT a rebuild)

The look is done and lives in the working tree. The plan turns that prototype into
a production feature:

- **Strip the dev-only test hook** (`window.__dictviz` / `window.__dictSynth` +
  the synthetic-spectrum branch) from the shipped page — it exists solely so the
  offline Playwright harness can preview the reactive state. The harness stays as a
  documented dev script in the SDD workspace, not in the page.
- **Viz code location:** keep the IIFE inline in `index.html` (recommended — the
  page is already one self-contained file) vs. extract to a served `web/dict.js`.
  Either way: tidy, comment, and keep it isolated (no collisions with the existing
  page script).
- **Audio asset mechanism:** `.gitignore` `web/dictionary.mp3`; ensure the hub
  image includes it (build/deploy step); confirm graceful degradation when absent.
- **Tests (pytest / `TestClient` — the seams, since a WebGL/canvas + Web-Audio
  loop is not unit-testable):**
  - `GET /dictionary.mp3` → 200 `audio/mpeg` when the file is present; **404 when
    absent** (both paths).
  - `GET /` contains the viz markers (`id="dictviz"`, `id="dictbar"`, the
    `/dictionary.mp3` reference) and the existing `test_root_serves_the_page` still
    passes.
  - Keep the **Playwright harness** as a documented, dev-only visual check (drives
    the synthetic spectrum, screenshots idle / mid / loud / scrolled). It is **not
    wired into CI** (CI is pytest-only), and that is called out so it isn't mistaken
    for coverage.
- **Confirm** perf (the stable full perimeter iterates all cells/frame but only
  draws visible rows), reduced-motion, and both themes.

## Out of scope / parked

- An automated **real-audio** playback/analysis test (headless audio decode is
  unreliable; the synthetic-spectrum harness + a manual check cover the look).
- Multiple tracks; per-viewer volume persistence; a visualiser toggle.
