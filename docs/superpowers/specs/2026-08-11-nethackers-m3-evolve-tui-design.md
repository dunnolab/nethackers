# M3 Evolve TUI — design (2026-08-11)

**Goal:** Replace the `rich.Live` scroll of the `evolve` command with an
interactive terminal UI: a pinned status bar over two tabs — **Tables** (the
per-seed episode tables) and **Mutation logs** (the coding agent's activity,
switchable per iteration and scrollable).

**Architecture:** A [Textual](https://textual.textualize.io) app,
`EvolveApp`. The existing synchronous `run_loop` runs unchanged inside a
Textual *worker thread*; it pushes updates to the UI through three optional
callbacks that hand off to the app thread-safely. When stdout is **not** a
TTY (piped, CI), `evolve` skips the app entirely and uses today's `rich.Live`
renderer — byte-for-byte unchanged.

**Tech stack:** Python, Textual (new dep), rich (already used). The loop,
operator, arena, and hub are untouched except for two additive callbacks.

## Global Constraints

- **Additive, no behavior change off the happy path.** The new callbacks
  (`on_line`, `on_log`, `on_state`) all default to no-ops. With them absent,
  `operator.run` and `run_loop` behave exactly as today, and every existing
  test stays on the current path. The `rich.Live` fallback in `cli.py` is the
  current code, moved behind a TTY check.
- **Never crash the run for a display concern.** Prettifiers and stream
  parsers tolerate any shape the agent emits (unparseable/unknown → skipped),
  mirroring the `_usage_tokens` isinstance-guard discipline already in
  `operator.py`. A display exception must never abort a mutation or eval.
- **The loop stays UI-agnostic.** `run_loop`/`operator` know nothing about
  Textual, tabs, or the backend's *name*. They forward raw lines and
  structured state; the CLI/TUI layer interprets them.
- **CLI-UX bar (repo standard):** aligned, readable, no raw tracebacks
  surfaced to the user; a non-TTY still produces useful output.

---

## Data flow

```
                    ┌──────────── Textual worker thread ────────────┐
  cli evolve  ─────▶│  run_loop(... report, on_episode, on_log,     │
    (EvolveApp)     │           on_state ...)                       │
       ▲            │     │           │          │         │        │
       │            │  on_state   on_episode  on_log    (report)    │
       │            └─────┼───────────┼──────────┼─────────┼────────┘
       │  call_from_thread│           │          │         │
       └──────────────────┴───────────┴──────────┴─────────┘
             StateMsg     EpisodeMsg   LogMsg    (ignored/footer)
                │             │           │
          status bar     Tables tab   Mutation-logs tab
        (+ live tokens from LogMsg, + eval step from EpisodeMsg)
```

The worker calls the callbacks synchronously from the loop; each callback
hands the payload to the app thread via `App.call_from_thread` (or
`post_message` — both are thread-safe in Textual). The app updates reactive
state / widgets on its own thread. No shared mutable state crosses threads
except through that hand-off.

---

## Callback contracts (the only loop/operator changes)

### `operator.py`
- `run_with_token_budget(..., on_line: Callable[[str], None] | None = None)`.
  The stdout loop already exists (`operator.py:41`); add, inside it:
  `if on_line is not None: on_line(line)` **before** the budget/timeout
  checks (so the last in-flight line is still surfaced). Raw line, verbatim.
- `ClaudeOperator.run` / `CodexOperator.run` gain `on_line=None` and pass it
  through to `run_with_token_budget`.
- New public helper `agent_tokens(backend: str, line: str) -> int` that
  dispatches to `_claude_tokens` / `_codex_tokens`. Used by the UI to keep a
  live token counter from the same stream (no second parse path invented).

### `loop.py` (`run_loop`)
- `on_log: Callable[[str, str], None] | None = None` — `(iter_tag, raw_line)`,
  e.g. `("iter 2/3", '{"type":"assistant",...}')`. Threaded into the operator
  as `operator.run(..., on_line=_log_cb(tag))` where `_log_cb` is a
  None-guarded closure exactly like the existing `_episode_cb`.
  The *backend name* is added by the CLI closure, not the loop.
- `on_state: Callable[[dict], None] | None = None` — emitted at every phase
  transition with the structured snapshot below. No-op default.

`on_state` payload (all keys always present):

```python
{
  "phase": str,      # "cold-start" | "mutating" | "gating"
                     # | "evaluating-dev" | "evaluating-held"
                     # | "registered" | "rejected" | "error" | "done"
  "iteration": int,  # 1-based current iter; 0 during cold-start
  "baseline_dev": float, "baseline_held": float,   # cold-start elite
  "best_dev": float, "best_held": float,           # current elite
  "wins": int,       # iterations that registered a new elite so far
  "tokens": int,     # last completed mutation's token total (0 until first)
  "detail": str,     # short reason/label ("no dev gain", "gate: ...", "")
}
```

Emission points in `run_loop`:
- after cold start → `phase="cold-start"` (sets baseline_* and best_* equal).
- iter start → `phase="mutating"`, `iteration=k`.
- gate start → `phase="gating"`.
- dev eval start → `phase="evaluating-dev"`, `tokens=<operator result>`.
- held eval start → `phase="evaluating-held"`.
- on register → `phase="registered"`, best_* updated, `wins+1`.
- on reject → `phase="rejected"`, `detail=<reason>`.
- on exception → `phase="error"`, `detail=<msg>`.
- loop end → `phase="done"`.

Static config (objective, backend, iterations, token_budget) is **not** in
the payload — the app receives it once at construction.

---

## The app (`src/nethackers/tui/`)

### `prettify.py` — pure agent-line → display-lines

`prettify(backend: str, line: str) -> list[PrettyLine]` where
`PrettyLine = tuple[str, str]` is `(kind, text)` and `kind ∈ {"assistant",
"tool", "result", "meta"}` (drives styling). Returns `[]` for lines to skip
(system init, tool results, blank, unparseable). Never raises.

**Claude** (`--output-format stream-json`), one JSON object per line:
- `type=="assistant"`: for each block in `message.content` →
  - `text` → `("assistant", <text>)`
  - `tool_use` → `("tool", f"{verb} {arg}")` where `verb` lowercases the
    tool name (`Edit`→`edit`, `Read`→`read`, `Bash`→`bash`, `Write`→`write`,
    else the raw name) and `arg` is `input.file_path` (edit/read/write) or
    `input.command` (bash) or `""`, truncated to ~80 chars.
- `type=="result"`: `("result", f"done · {subtype} · {out} tok")` where
  `out` sums `usage` output tokens (via `_usage_tokens`).
- `type in {"system","user"}` or anything else → `[]`.

**Codex** (`exec --json`): analogous best-effort mapping over codex's event
objects — assistant/message text → `("assistant", …)`, command/tool
execution → `("tool", …)`, final/turn-complete → `("result", …)`; any
object without a recognized shape → `[]` (never raw-dumped, never crashes).
The exact codex event keys are refined during manual acceptance; the
contract (never raise, `[]` on unknown) is fixed. Claude is the primary
backend for the acceptance bar.

All parsing reuses/mirrors `operator._usage_tokens`' defensive guards.

### `status.py` — pure status formatting + the widget

`format_status(cfg, state, *, live_tokens, eval_step, elapsed_s) -> tuple[str, str]`
returns the two status lines as rich-markup strings. Pure and unit-tested:

- **line 1 (live step)** — one branch per `phase` value (no unhandled state):
  - cold-start → `COLD START · scoring baseline …`
  - mutating → `MUTATING iter {k}/{N} · {live_tokens}/{budget} tok · {m:s}`
  - gating → `GATING iter {k}/{N} · smoke …`
  - evaluating-dev/-held → `EVALUATING {dev|held} · iter {k}/{N} · ep {i}/{n} · x̄ {mean:.3f}`
    (`eval_step` carries `i,n,mean`, fed from `EpisodeMsg`)
  - registered → `✓ REGISTERED iter {k}/{N}` (until the next state replaces it)
  - rejected → `✗ iter {k}/{N} · {detail}`
  - error → `✗ ERROR · {detail}`
  - done → `DONE · {wins}/{N} registered`
- **line 2 (best-so-far):**
  `best dev {best_dev:.3f} (base {baseline_dev:.3f}) · held {best_held:.3f} · {wins} win(s)`

Token counts render compact (`12.5k/40k`). `StatusBar(Static)` holds the
current `cfg`+`state` and re-renders on update; a 1 Hz timer refreshes the
elapsed clock and the live-token figure during mutation.

### `app.py` — `EvolveApp`

- **Layout:** `StatusBar` (pinned, `dock="top"`), then `TabbedContent` with
  `TabPane("Tables")` and `TabPane("Mutation logs")`, then a `Footer` with
  key hints. Panes render inside a centered column (`align: center`, a
  `max-width` container with side gutters), text left-aligned within.
- **Tables tab:** a `VerticalScroll` of `Static` widgets, one per batch. The
  running batch's `Static` is updated in place with
  `hubclient.live.episode_table(label, rows, done=False)`; on the next
  batch it's finalized (`done=True`) and a fresh `Static` is mounted. Reuses
  the existing, tested `episode_table` verbatim.
- **Mutation-logs tab:** horizontal split — left a `ListView` of iteration
  tags (newest on top, a `●` marker on the still-running one); right a
  `RichLog` (scrollable). Selecting an item renders that iteration's buffered
  prettified lines into the `RichLog`. New lines for the *currently selected*
  iteration append live; for other iterations they buffer silently. The
  running iteration is auto-selected when it starts.
- **State:** the app keeps `logs: dict[str, list[PrettyLine]]` (per iter
  tag), the latest `state` dict, and derived `live_tokens` per tag (summed
  via `agent_tokens`). No MVP cap on buffered lines (a few iterations ×
  hundreds of lines is fine; note as a future cap).
- **Messages** (posted from the worker via `call_from_thread`):
  `StateMsg(state)`, `EpisodeMsg(label, ep)`, `LogMsg(tag, backend, line)`.
  Handlers update the status bar, the tables pane, and the logs pane/token
  counter respectively.
- **Worker:** `@work(thread=True)` runs an injected `run(callbacks) -> results`
  (defaults to a `run_loop` partial closing over the CLI's config; injectable
  so `run_test` drives a fake loop with no docker) with the three callbacks
  bound to `self.call_from_thread(self._on_*, ...)`. On completion it posts a
  final `StateMsg(phase="done")`; the app **stays open** so the user can
  scroll tables/logs, and exits on `q`.
- **Bindings:** `Tab`/`shift+Tab` cycle tabs (Textual default), `q`/`ctrl+c`
  quit, `↑↓`/`PageUp`/`PageDown` scroll the focused widget, and within the
  logs tab the `ListView` selection (↑↓ when focused, or click) picks the
  iteration.
- **Errors:** a callback raising is caught and dropped (logged to a debug
  buffer, never surfaced); a `run_loop` exception ends the worker and posts
  `phase="error"` with the message shown in the status bar (the loop already
  guards per-iteration, so this is only a catastrophic failure).

---

## CLI wiring (`cli.py`)

```
if args.cmd == "evolve":
    operator = {...}[args.operator]()
    if err.is_terminal and args.output != "json":
        EvolveApp(cfg=..., run=lambda cbs: run_loop(..., **cbs)).run()
    else:
        <current rich.Live path, unchanged>
```

The two paths call the **same** `run_loop`. The TUI path binds `on_log`
(closing over `args.operator` for the backend), `on_state`, and `on_episode`;
the fallback binds only `report` + `on_episode` as today. The TUI takes over
the terminal via the alternate screen and restores it on exit; the final
`done · N/M registered` line is printed to `err` after the app exits so it
survives in scrollback.

---

## Testing

**Pure units (the coverage backbone):**
- `test_tui_prettify.py` — canned claude lines (assistant text; `tool_use`
  Edit/Read/Bash; result; system/user skipped; a `message`-is-a-string line
  → `[]` not a crash) and a couple of codex lines. Assert exact
  `(kind, text)` output and that garbage → `[]`.
- `test_tui_status.py` — `format_status` for mutating / evaluating /
  cold-start / done states; compact token formatting; baseline vs best line.
- `test_harness_operator.py` — `on_line` receives each stdout line (fake
  popen), and still stops on budget/timeout; `agent_tokens` dispatch.
- `test_harness_loop.py` — with `on_state`/`on_log` set, the loop emits a
  `cold-start` state then a `mutating` state per iteration, with correct
  `iteration`/`wins`/`best_*`; with them **unset**, existing tests are
  unaffected (proves the no-op default).

**App wiring (headless):**
- `test_tui_app.py` — Textual's async `App.run_test()`: mount `EvolveApp`,
  post `StateMsg`/`EpisodeMsg`/`LogMsg`, assert the status text updates, a
  table row appears, and a log line lands in the selected iteration. One or
  two smoke tests — proves the message→widget wiring, not the visuals.
  (Adds `pytest-asyncio` as a dev dep, or uses Textual's test harness.)

**Manual acceptance (TTY-only):** the actual look, tab switching, scrolling,
and log readability are verified by running `nethackers evolve …` in a real
terminal — the same manual-acceptance gate the loop itself uses. Codex
prettifier keys are refined here.

---

## Out of scope (MVP)

- `evolve -o json` machine output (backlog #3) — the non-TTY branch keeps
  the current renderer for now.
- Buffered-log truncation / persistence to disk.
- Mouse-driven scrolling polish, search-in-logs, copy.
- Any change to the loop's algorithm, the arena, the hub, or the operator's
  budget/timeout semantics.
- The deferred backlog items (model pin, provenance, heartbeat as a separate
  feature, silent-hang timeout) — unaffected, still deferred.
