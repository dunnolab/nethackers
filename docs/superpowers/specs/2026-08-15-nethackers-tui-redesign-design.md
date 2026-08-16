# NetHackers TUI redesign — design (2026-08-15)

**Goal:** Turn `nethackers` from a flag-only CLI (that opens a single-purpose
`evolve` TUI) into a themed, NetHack-flavored **dashboard app** you live in —
a home screen with your identity, boards, map, elites, and runs — while
keeping the flag-CLI fully usable for agents and scripts. The centerpiece is a
redesigned **evolve monitor**: a live, in-place view of the *evolutionary
event* (parent → candidate → eval → lineage), replacing today's
append-a-table-per-batch scroll.

**Tech stack:** Python, [Textual](https://textual.textualize.io) (already a
dep), rich (already used). No new runtime dependencies. Every change to the
loop/operator/hub layers is *additive*.

This design was reached through brainstorming; the four product decisions and
their rationale are recorded in §1 so the "why" survives.

---

## 1. Decisions (resolved forks)

1. **Home = dashboard, not launcher or mission-control.** Bare `nethackers`
   (on a TTY) opens a themed shell with sidebar/tab navigation. Panels
   **refresh on entry** — no background polling. `evolve` gets its own live
   monitor screen. (Rejected: a thin launcher menu; and full live
   mission-control with hub polling — the latter is a designed-in growth path,
   not this pass.)

2. **Evolve monitor = live evolution-event dashboard, "scope A".** A fixed
   frame that updates in place and centers the *mutation* (parent, token cost,
   candidate, eval, lineage). It renders only data the loop produces **today**;
   the sole loop change is four additive `on_state` keys. "Influences" render
   as a visible placeholder that lights up when the loop later selects them.
   (Rejected: bare eval-grid framing; the full NetHack dungeon-columns view;
   and plumbing real influence-selection now — that's an algorithm change with
   its own brainstorm.)

3. **NetHack theming = Medium (accents + iconic set-pieces).** A cohesive tty
   palette + status glyphs everywhere, plus three borrowed set-pieces: the
   ranking board as NetHack's **high-score screen**, a rejected iteration as
   the **RIP tombstone**, and the **bottom status-line** motif on the monitor.
   (Rejected: Light = palette/glyphs only; Heavy = the attainment map as an
   explorable dungeon — a designed-in growth path.)

4. **Login = real GitHub identity, unified.** Reuse the existing device flow to
   mint a GitHub user token; store `{login, token}` locally; add
   `login`/`logout`/`whoami`; wire the stored identity into the TUI (top bar,
   "you" highlight, "your solutions") and into `evolve`/`register` attribution
   defaults. (Rejected: display-only local name; deferring identity.)

---

## 2. Global constraints

- **Additive, no behavior change off the new path.** Every change to
  `loop.py`, `operator.py`, `render.py`, and `register.py` is additive with
  defaults that preserve today's behavior. All existing tests stay on their
  current path and stay green.
- **Agents stay first-class.** The flag-CLI is untouched: `nethackers evolve …`,
  `board …`, `eval …`, `register …` behave exactly as today, including
  `-o json/table/plain` and the non-TTY `rich.Live` fallback for `evolve`. The
  TUI is never *required* and never emitted into a pipe.
- **Never crash a run for a display concern.** The evolve monitor keeps the
  existing discipline: display handlers are `@_guarded` (exceptions logged to a
  debug buffer, dropped), prettifiers/parsers tolerate any shape, and any app
  error is re-raised into `main()`'s single top-level guard — no raw traceback
  ever reaches a user (repo CLI-UX standard).
- **The loop stays UI-agnostic.** `run_loop`/`operator` know nothing about
  Textual, screens, or theming. They forward raw lines and structured state;
  the TUI layer interprets them.
- **Pure formatting is unit-tested; widgets get smoke tests.** All string/art
  formatting lives in pure functions (`art.py`, `theme.py`, `status.py`, home
  panel formatters) tested as strings/objects — the pattern already
  established by `test_tui_status.py`/`test_tui_prettify.py`. Textual screens
  get light smoke tests like today's `test_tui_app.py`.

---

## 3. Architecture & entry rules

One Textual app, **`NetHackersApp`**, replaces the standalone `EvolveApp`.
Today's `EvolveApp` becomes one **screen** (`EvolveScreen`) inside it; its
proven mechanics carry over unchanged:

- the synchronous `run_loop` runs in a `@work(thread=True, exit_on_error=False)`
  worker,
- callbacks hand payloads to the app thread via `call_from_thread`,
- display handlers are wrapped with `_guarded`,
- `app.error` is re-raised by the CLI so `main()`'s hub/docker handlers fire on
  the original exception.

Navigation is a top tab-bar over a `ContentSwitcher`; switch with `Tab`/`1–6`
or an accelerator key. Screens: **⌂ Home · ⚔ Evolve · ♛ Boards · ▚ Map ·
⚑ Elites · ▶ Runs**. Each screen **refreshes its data when entered** (no
background workers except the evolve run itself).

**Entry rules in `cli.py`:**

| Invocation | Behavior |
|---|---|
| `nethackers` on a TTY (not `--no-tui`) | Launch `NetHackersApp` at Home |
| `nethackers` piped / non-TTY / `--no-tui` | `parser.print_help()` (today's behavior) |
| `nethackers evolve …` on a TTY | Launch `NetHackersApp` straight into `EvolveScreen` |
| `nethackers evolve …` piped / non-TTY / `-o json` | Existing `rich.Live` fallback, byte-for-byte |
| `nethackers board/map/elites/…` | Unchanged (`emit(...)` with `-o`) |

A new top-level `--no-tui` flag forces help/plain output even on a TTY (escape
hatch for humans who want the old front page). Bare-invocation TTY detection
uses `sys.stdout.isatty()`, matching the existing `evolve` check.

---

## 4. Home dashboard

```
┌ NetHackers ─────────────────────────── @castiel · hub:localhost ┐
│  ⌂ Home   ⚔ Evolve   ♛ Boards   ▚ Map   ⚑ Elites   ▶ Runs       │
├──────────────────────────────┬──────────────────────────────────┤
│ your solutions   search      │ ♛ random · leaderboard           │
│  Wiz-Elf-Cha-Mal .44  #7a3f  │  1  .510  @vale    Dlvl:26       │
│  Val-Dwa-Law-Fem .30  #a19c  │  2  .440  @castiel Dlvl:22 ◀ you │
│                              │  3  .290  @grunt   Dlvl:14       │
│ recent runs      local       ├──────────────────────────────────┤
│  ● iter 2/3  wiz-elf  ✓1     │ your attainment · wiz-elf-cha-mal│
│    r-0815-…  dev .44 held .41│  Dlvl ▓▓▓▓▓▓░░░░░░  best 26/50   │
│  ○ done      val-dwa  ✗0     │  ★ ascended ×0   ☠ deepest death │
└──────────────────────────────┴──────────────────────────────────┘
  Tab/1-6 switch · e evolve · ⏎ open · l login · q quit
```

**Panels and their exact data sources (all already exist):**

- **your solutions** — `HubClient.search(owner=login)`. Rows carry the
  registered solutions for the logged-in login (identity + digest + evidence).
  The score (e.g. `.44`) is the mean progression read from each solution's
  stored evidence. Empty/"log in to see yours" when no credentials.
- **♛ leaderboard** — `HubClient.board("random")` (the north-star objective).
  Board rows carry `rank`, `owner`, `solution_digest`, and the metric
  (`mean_progression`/`median_progression`/`ascensions`). The row where
  `owner == login` is highlighted (`◀ you`). The `Dlvl:` shown is **derived**
  from the progression score via `arena.progress.ACHIEVEMENTS` (nearest
  milestone) — a display nicety, since board rows carry a 0–1 score, not a
  depth.
- **recent runs** — local `~/.nethackers/evolve/runs/<id>/run.json` +
  `metrics.jsonl` (the `runlog` layout). Shows the last few runs with their
  win/loss tally; `⏎` opens the run detail (Runs screen).
- **your attainment** — `HubClient.attainment(identity)` for the identity of
  the user's best solution (highest score among `search` results); milestones
  mapped to depth via `arena.progress.ACHIEVEMENTS`. Compact depth bar +
  ascension/death summary.

Boards/Map/Elites full screens reuse `hubclient.render` renderers, re-skinned
via the theme; the Runs screen reads the local `runs/` tree.

---

## 5. Evolve monitor (`EvolveScreen`)

```
┌ ⚔ Evolve · wiz-elf-cha-mal · claude ──────────── iter 2/3 ┐
│ PARENT   #7a3f · gen 1 · dev .30  held .28                │
│    │                                                      │
│    ▼ mutating ⠙  92k/200k tok · ⏱3:14                     │
│ CANDIDATE  ▸ editing strategy.py: altar tuning…          │
│    ┆ influences  —  (lights up when the loop selects them)│
├──────────────────────────────────────────────────────────┤
│ eval · dev   ▓▓▓▓░░░░  x̄.38  2/8   ★1 ☠2                  │
│ eval · held  ┄ pending                                    │
├──────────────────────────────────────────────────────────┤
│ lineage  seed → #a1 → #7a3f ● → ?     best dev .44 Δ+.14  │
│ iterations  1 ✗ no-dev-gain   2 ▶ running…               │
├──────────────────────────────────────────────────────────┤
│ wiz-elf-cha-mal  gen:1 tok:92k T:3:14 best:Dlvl26 w:1     │  ← status-line motif
└──────────────────────── agent ▸ Tab for full log ────────┘
```

**Behavior:**

- **In-place, never scrolls.** The `eval` line resets each batch (dev, then
  held-out) instead of mounting a new table. The `iterations` ledger grows one
  compact row per iteration (`k ✓ registered` / `k ✗ <reason>`).
- **Live.** ⠙ spinner during mutation, ticking token counter (from the same
  `agent_tokens` stream used today), filling eval bar, status glyphs (★/☠)
  popping in as episodes finish. The `T:` clock ticks on a `set_interval`, as
  today.
- **On reject.** The **RIP tombstone** set-piece flashes in the CANDIDATE slot
  (identity + `iter k` + reason), then the ledger records `k ☠ <reason>`.
- **Agent log.** `Tab` opens the full per-iteration mutation log
  (today's Mutation-logs pane, kept — prettified stream, per-iteration
  selectable). It is the *only* scrollable region, inside a fixed pane.
- **Influences.** Rendered as the literal `—` placeholder until the loop emits
  them (see §7). No fabricated data.

### Loop change — four additive `on_state` keys (`loop.py`)

`run_loop` already tracks everything needed. Capture a **parent snapshot** and
`generation` and add them to the `_emit` payload; all four keys default to
safe values so a caller that ignores them is unaffected.

- After cold start: `parent_digest, parent_dev, parent_held = seed_digest,
  dev_fit0, ho_fit0`; `generation = 0`.
- At each iteration start (before the `mutating` emit):
  `parent_digest, parent_dev, parent_held = elite.digest, elite.dev_fitness,
  elite.heldout_fitness`; `generation = wins`.
- `_emit(...)` payload gains: `"parent_digest"`, `"parent_dev"`,
  `"parent_held"`, `"generation"`.

Semantics: for a given iteration's `mutating`/`gating`/`evaluating-*`/
`rejected`/`registered` emits, the parent snapshot is the elite that was
mutated (stable across the iteration); `generation` is the number of accepted
elites before this iteration (seed = 0). The monitor keys the PARENT panel off
the `mutating` emission and updates lineage on `registered`.

`EvolveConfig` (constructed once, in `cli.py`) already carries objective,
backend, iterations, token_budget — unchanged. The monitor renders parent,
lineage, and best/Δ from `on_state`; per-seed eval from `on_episode`; token
stream from `on_log` — all existing callbacks.

---

## 6. Login / identity

**Storage — `src/nethackers/hubclient/credentials.py` (new):**

- `Credentials` = `{login: str, token: str}`.
- `path()` → `~/.nethackers/credentials.json` (the `~/.nethackers/` dir is
  already used for `evolve` workdirs).
- `load() -> Credentials | None`, `save(creds)` (writes `chmod 0600`),
  `clear()`.
- `whoami_from_token(token, http=httpx) -> str` — resolves a token to a GitHub
  login via `GET https://api.github.com/user`, mirroring
  `hub.auth.GitHubAppAuth.resolve` (injectable `http` for tests).

**Device flow — refactor `src/nethackers/hubclient/register.py`:**

- Extract the token-minting half of `register_solution` (device-code request +
  poll loop, lines ~64–94) into
  `device_login(*, client_id=DEFAULT_CLIENT_ID, http=httpx, prompt=print,
  sleep=time.sleep) -> str` returning the `access_token`.
- `register_solution` becomes `token = device_login(...); return
  hub.register(token=token, …)` — identical behavior, now reusing the helper.

**CLI verbs (`cli.py`):**

- `nethackers login` — run `device_login()`, resolve the login via
  `whoami_from_token`, `credentials.save({login, token})`, print
  `logged in as @login`.
- `nethackers logout` — `credentials.clear()`.
- `nethackers whoami` — print the stored login (with `-o json` support); exit
  nonzero if not logged in.

**Wiring:**

- `evolve`'s `--owner`/`--token` and `register`'s auth **default to** stored
  credentials when the flags are not given; an explicit flag always wins (so
  agents and the dev `dev`/`dev-token` stub path are unchanged).
- TUI top bar shows `@login · hub:<host>` (or `guest` when logged out); the
  `l` key runs the device flow in a worker (shows verify URL + user code,
  polls), then saves and refreshes the bar.
- `owner == login` drives the board "you" highlight and the "your solutions"
  panel.

**Reality note.** `DEFAULT_CLIENT_ID` is currently a placeholder
(`Iv1.nethackers-dev`); no live GitHub App exists yet. Login works
end-to-end against a real App once `NETHACKERS_CLIENT_ID` is set. Until then it
is exercised via injected `http`/`prompt`/`sleep` in tests (mirroring
`test_hubclient.py`) and the `dev`/`dev-token` stub path remains the local
escape. This is an honest limitation, not new debt.

---

## 7. Theming system

**`src/nethackers/tui/theme.py` (new) — the shared visual language:**

- A cohesive NetHack tty palette exposed as Textual CSS variables (the classic
  16-color DECgraphics feel: green/yellow/cyan/magenta accents, `@` in white).
- Glyph + status maps, reusing the existing `hubclient.live._STATUS_STYLE` and
  `_progress_style` so the monitor, boards, and home agree on colors: `★`
  ascended, `☠` died, `@` running, `Dlvl` depth.
- One Textual `CSS`/theme string the app applies globally; screens reference
  variables, never hard-coded colors.

**`src/nethackers/tui/art.py` (new) — pure set-pieces (unit-tested as strings):**

- `tombstone(lines: list[str]) -> str` — the RIP graveyard headstone; centers
  up to N short lines (identity, `iter k`, reason).
- `highscore_table(rows, you: str | None) -> Table` — board rendered in the
  NetHack high-score idiom (`rank  score  Identity  Dlvl  @owner`), the `you`
  row emphasized.
- `status_line(fields: dict) -> str` — the bottom status-line motif
  (`identity  gen:  tok:  T:  best:Dlvl  w:`).

The `Dlvl` values in `highscore_table` and `status_line` are **derived** from
progression scores via `arena.progress.ACHIEVEMENTS` (nearest milestone), not a
field on the row — see §4. A small pure `score_to_dlvl(score) -> str` helper
(unit-tested) owns that inverse lookup.

**Board "you" highlight — additive `render.py` change:** `render_board(...,
you: str | None = None)`; `None` (the CLI default) preserves today's output.
The TUI boards screen passes `you=login`.

---

## 8. Module & test plan

**New files:**

- `src/nethackers/tui/theme.py` — palette + glyph maps.
- `src/nethackers/tui/art.py` — RIP / high-score / status-line set-pieces.
- `src/nethackers/tui/screens/home.py`, `boards.py`, `map.py`, `elites.py`,
  `runs.py`, `evolve.py` — one screen each (`evolve.py` is the reworked
  `EvolveApp` body as `EvolveScreen`).
- `src/nethackers/hubclient/credentials.py` — storage + `whoami_from_token`.

**Changed files (all additive):**

- `src/nethackers/tui/app.py` — `NetHackersApp` root: theme, tab-bar +
  `ContentSwitcher`, screen registry, top identity bar; hosts `EvolveScreen`.
- `src/nethackers/cli.py` — bare-TTY launch, `login`/`logout`/`whoami` verbs,
  `--no-tui`, credential-defaulting for `evolve`/`register`, launch `evolve`
  into `EvolveScreen`.
- `src/nethackers/harness/loop.py` — four additive `on_state` keys +
  parent-snapshot/`generation` locals.
- `src/nethackers/hubclient/register.py` — extract `device_login()`.
- `src/nethackers/hubclient/render.py` — optional `you=` on `render_board`.

**Tests:**

- `tests/test_tui_art.py` — `tombstone`, `highscore_table`, `status_line`, and
  `score_to_dlvl` (boundary milestones, monotonicity) as strings/objects (pure).
- `tests/test_tui_theme.py` — glyph/status maps and palette variable presence.
- `tests/test_tui_home.py` — home panel formatters (pure) + a light screen
  smoke test.
- `tests/test_tui_status.py` — extend for the new `on_state` keys in the status
  line.
- `tests/test_credentials.py` — round-trip, `chmod 600`, missing-file → `None`,
  `whoami_from_token` with injected `http`.
- `tests/test_cli_login.py` — `login`/`logout`/`whoami` wiring with
  `device_login`/`credentials` monkeypatched (no network); credential-default
  precedence vs explicit `--owner/--token`.
- `tests/test_cli.py` / `test_cli_evolve.py` — assert entry rules: bare piped →
  help; bare `--no-tui` → help; `evolve` piped → `rich.Live` path unchanged.
- `tests/test_harness_loop.py` — assert the four new `on_state` keys appear with
  correct parent/generation values across cold-start → iteration → register.

All existing tests remain valid because every loop/operator/render/register
change is additive with behavior-preserving defaults.

---

## 9. Explicitly NOT in this pass (YAGNI)

- **Real influence selection** — the loop still briefs the agent only from the
  parent's evidence. The monitor's influences slot is a placeholder. (Algorithm
  change; own brainstorm.)
- **The dungeon-map attainment metaphor** (Heavy theme) — Map stays a themed
  grid/table this pass.
- **Live-home / background hub polling** (mission-control) — panels refresh on
  entry only.
- **Multi-parent / branching lineage** — lineage is the real linear chain
  today; the strip is built to accept a tree later.

Each has a designed-in slot (placeholder influences line, themed Map screen,
refresh-on-entry that could become polling, lineage strip that accepts a tree)
so growth is additive.

---

## 10. Risks & open questions

- **`ContentSwitcher` vs `Screen` stack.** Both are viable Textual patterns;
  the implementation plan should pick one (leaning `ContentSwitcher` for a
  persistent tab-bar) — not a design blocker.
- **"Your attainment" identity choice.** Picking the user's *best* identity for
  the home mini-panel is a heuristic; if a user has none, the panel invites
  login/first run.
- **Real GitHub App.** End-to-end login against github.com is gated on
  registering the App and setting `NETHACKERS_CLIENT_ID` (see §6) — out of
  scope here, but the wiring is ready.
