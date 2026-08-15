# Background Runs — Jump In / Jump Out of Evolutions (Design)

**Status:** approved (2026-08-15), building.

## Goal

Evolution runs become **background work owned by the app**, not something
that owns the screen. You can start several runs, leave a run's monitor to
explore Home / Leaderboard / Frontier / Elites, and jump back into any
run's live monitor. The **Runs** tab is the hub for ongoing + past runs.

Approved decisions: **multiple concurrent runs**; **starting a run opens its
monitor** (esc leaves it running).

## The model (approved)

- A run's worker + live state move out of the monitor screen into an
  app-level **`Run`** object. The worker keeps running regardless of what's
  on screen.
- The **monitor becomes a view onto a `Run`**: opening it backfills from the
  run's current state, then live-updates; `esc` detaches the view (the run
  keeps going). Any run can be opened, left, reopened.
- The **Runs tab** lists ongoing runs (live) on top and past runs (from
  disk) below; select an ongoing run to jump in; **Stop** is explicit.
- Start flow: the Evolve form's Start and `nethackers evolve` both register
  a run and open its monitor; esc drops to the dashboard, run still going.

**Consequences (approved):**
1. `nethackers evolve` on a TTY becomes an interactive session (opens the
   dashboard, auto-starts the run + monitor, roam, quit when done). The
   scripted path (`--no-tui` / `-o json`) is unchanged: runs to completion,
   prints the summary, exits.
2. Runs live for the **app session** — background threads inside the running
   app; leaving the monitor keeps them going, quitting (`q`) stops them.
   **Not** a detached daemon (explicitly out of scope).

## Components

### `Run` (new — `tui/run.py`)
An app-owned record of one evolution, holding the data a monitor needs to be
rebuilt from scratch (backfill) plus live worker plumbing:

- Identity/config: `rid: str`, `cfg: EvolveConfig`, `started_at: float`.
- Status: `status: "running" | "done" | "failed" | "stopped"`, `results`,
  `error`.
- Accumulated monitor state (moved verbatim from `EvolveScreen`'s instance
  vars): `state: dict` (latest `on_state`), `chain: list[str]`,
  `ledger_rows: list[tuple[int,bool,str]]`, `counts: dict[str,int]`,
  `eval_step: tuple|None`, `batches: list[Batch]` where a `Batch` is
  `{label, rows_by_index: dict[int,dict], done: bool}` (append-only, in
  arrival order; keyed by index so out-of-order eval still reads in batch
  order), `logs: dict[tag, list[(kind,text)]]`, `meters: dict[tag, Meter]`,
  `sel_tag`, `mut_start: float`.
- Control: `stop: threading.Event`.
- Data mutators, called ONLY on the UI thread (from the app worker via
  `call_from_thread`): `apply_state(s)`, `apply_episode(label, ep)`,
  `apply_log(tag, line)` — these update the fields above (the same reductions
  `EvolveScreen._apply_*` do today), so the `Run` is always a complete,
  replayable snapshot.
- Derived read helpers for the monitor/list: `running_tag()`,
  `live_tokens()`, `elapsed()`, `split()`.

### App run registry (`NetHackersApp`)
- `self._runs: dict[str, Run]` (insertion-ordered; ongoing + finished this
  session).
- `start_run(plan: EvolvePlan) -> Run`: build a `Run` from `plan.cfg`/`rid`,
  register it, spawn the app-level worker, and (default) open its monitor.
- The **app-level worker** (`@work(thread=True)` on the App) drives
  `plan.run(callbacks)` where each callback is
  `lambda ...: self.call_from_thread(self._on_run_event, run, kind, payload)`.
  `_on_run_event` (UI thread): `run.apply_*(...)`, then — if that run's
  monitor is currently mounted — forwards to the monitor's incremental
  render so the open view updates live. The `stop` event is passed in
  `callbacks["stop"] = run.stop`. On worker completion/exception it sets
  `run.status`/`results`/`error`.
- `open_run(rid)`: push a `RunMonitor(run)` screen.
- `stop_run(rid)`: `run.stop.set()`.
- `on quit`: set every running run's stop event (best-effort) before exit.

### `RunMonitor` (refactor of `EvolveScreen` → `tui/screens/monitor.py`)
Same cockpit (PARENT→CANDIDATE→eval→lineage→ledger), Monitor/Agent-log tabs,
status line. Changes:
- Constructed with a `Run` (not a `run` callable). It **owns no worker**.
- `on_mount`: **backfill** the whole view from the run — mount a table per
  `batch` (done ones final, the current one live), populate the log list +
  selected log, render the cockpit/status from `run.state`. Then it's live:
  the app forwards `apply_*` events (only while mounted) to incremental
  `render_state/render_episode/render_log` that update the current batch +
  cockpit + status (past batches, once done, aren't re-touched).
- `esc` → `dismiss()` only (NO stop — the run keeps running). A separate
  **Stop** binding (`s`) sets `run.stop`.
- `_guarded`/`_rows_in_order`/`_slug` reused from `tui/_util.py`.

### `RunsView` (rework — `tui/screens/runs.py`)
- Top: **ongoing runs** from `app._runs` (status == running) — one row each:
  `objective · phase · gen · wins · tokens · elapsed`, refreshed on a
  `set_interval` tick (~1s) from the live `Run`s.
- Below: **past runs** (this session's finished `Run`s + on-disk history via
  the existing `read_runs`), deduped by rid.
- Selecting an ongoing run → `app.open_run(rid)`. A **Stop** affordance on the
  selected ongoing run → `app.stop_run(rid)`.
- Fits the modal 2D nav: the run rows are the section's focusable elements
  (Enter opens, a Stop key stops).

### Start flows
- **Evolve form** (`EvolveForm.on_button_pressed`): `prepare_evolve(params)`
  → `self.app.start_run(plan)` (registers + opens the monitor) instead of
  pushing an `EvolveScreen` itself.
- **CLI `nethackers evolve`** (`cli.py`):
  - TTY (not `--no-tui`, not `-o json`): launch `NetHackersApp` with an
    initial plan to auto-start; `on_mount` → `start_run(plan)` (opens the
    monitor). `app.run()` blocks as the interactive session; quit stops
    runs; exit 0. A cold-start failure surfaces in the monitor (status
    `failed`), the app stays up.
  - Headless (`--no-tui` / `-o json` / non-TTY): unchanged — `plan.run(...)`
    to completion, print the `N/M registered` summary, exit; friendly
    hub/docker error handling preserved.

## Navigation

- The dashboard's modal 2D nav is unchanged; it already yields while a screen
  is pushed (`len(screen_stack) > 1`), so the `RunMonitor` owns its keys.
- `RunMonitor`: `esc` dismisses to the dashboard (lands on the Runs tab),
  `s` stops, `q` quits the app (stopping runs). Reopen from the Runs tab.

## Testing

- `Run`: `apply_*` reductions match the old `EvolveScreen._apply_*` (state,
  out-of-order episode ordering, meter totals, chain/ledger/counts); status
  transitions on completion/error/stop.
- App registry: `start_run` registers + spawns a worker (fake `plan.run`
  that drives callbacks off a thread); leaving/reopening a monitor doesn't
  stop the run (the worker still receives events; the `Run` keeps
  accumulating); `stop_run` sets the event; quit stops all.
- `RunMonitor`: backfills a pre-populated `Run` (tables/logs/status present
  on mount without any live events); a subsequent forwarded event updates
  the current batch; `esc` dismisses without setting `run.stop`; `s` sets it.
  **Visual check (controller): render backfill + live, confirm no layout
  break.**
- `RunsView`: lists an ongoing `Run` + a past one; Enter on an ongoing run
  calls `open_run`; Stop calls `stop_run`; live tick refreshes progress.
- CLI: TTY evolve starts a run + opens the monitor (mock plan); headless
  evolve unchanged (existing tests).

## Out of scope
- Detached/daemon runs that survive app exit (would need a separate process
  + IPC/attach).
- Persisting live in-memory monitor state to disk for cross-session replay
  (past runs still come from the existing on-disk `read_runs`).
