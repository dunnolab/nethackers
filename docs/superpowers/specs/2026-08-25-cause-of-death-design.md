# Cause of Death — design (v1)

Date: 2026-08-25
Status: proposed (awaiting review)

## Motivation

NetHack death messages — *"Killed by a jackal"* — are a community meme. When a
"superhuman" policy dies to a newt on Dlvl 1, that's a shareable moment. We
collect the cause of death for every evaluated episode and surface it two ways:

- **Per-policy**, in the user's TUI, so they can giggle at their own run's death
  distribution ("your nemesis is the sewer rat").
- **Collectively**, in the hub, so we build a shared corpus of how policies die.

## Proven feasibility (spike, 2026-08-25)

Confirmed by a live probe against the pinned build (`nle==1.3.0`), mirroring the
arena's exact `NetHackChallenge` construction:

- NetHack is compiled with `XLOGFILE` on. At game end it appends a tab-separated
  record to `{env.nethack._vardir}/xlogfile`; the `death=` field is the verbatim
  killer string (`death=killed by a jackal`, `death=starved to death`,
  `death=petrified by a chickatrice`, `death=quit`).
- The record must be read **after the episode loop breaks but before
  `env.close()`** — NLE deletes the per-episode temp dir on close.
- `how_done()` / `end_status` are category-only (no killer name); the tty
  tombstone is auto-consumed inside a single `step()` and never reaches the bot.
  **The xlogfile is the only viable source.**
- The full record also carries `role · race · gender · align · turns · deathlev ·
  points · while=` — out of scope for v1 but available later.

## Scope

**In (v1):**
1. Capture the raw `death=` string per episode as `cause_of_death` and thread it
   end-to-end (arena → contracts → evidence → local runs + hub).
2. **M1 (local, ship first):** persist a per-iteration cause counter locally; show
   a "☠ Causes of Death" panel in the TUI.
3. **M2 (collective):** a hub `deaths` table + `GET /deaths` + `HubClient.deaths`
   + a `nethackers deaths` CLI (honoring `-o json`).

**Out (fast-follow, explicitly deferred):** monster parsing / taxonomy, a
"Nemesis" highlight, Wall of Shame (trivial-mob list), Deadliest Monster,
rotating tombstone, ragequit / self-inflicted buckets, a TUI hub board section,
and any use of `while=` / `conduct=` / `achieve=` / role / race.

## Data rule — what counts as a "cause of death" (v1)

Store the **verbatim** `death=` string; group by exact string (no monster
parsing). Genuine deaths only:

```
raw = <death= field of the last xlogfile line>      # e.g. "killed by a jackal"
if raw is None:                       cause_of_death = None
elif is_ascended:                     cause_of_death = None   # a win, not a death
elif raw.split()[0] in {"quit","escaped","ascended"}:
                                      cause_of_death = None   # gave up / timed out / won
else:                                 cause_of_death = raw
```

Rationale: NLE reports quit/abort/truncation with `end_status == DEATH` and
writes `death=quit`, so `end_status` cannot distinguish a real death from a
stall. Filtering on the string keeps the board meaningful. ("Ragequit count" is
a deliberate fast-follow, not a v1 omission.)

## Design

### A. Collection (arena + contracts)

- **`contracts/models.py`** — add `cause_of_death: str | None = None` to
  `TrajectoryResult`, as a **defaulted trailing field** (same backward-compat
  pattern as `character` / `milestone`). `to_dict` includes it; `from_dict`
  (`cls(**value)`) absorbs it automatically, and old dicts lacking it → `None`.
- **`arena/environment.py`** — add `cause_of_death: str | None` to
  `EnvironmentMetrics`; add a private `_read_death_string()` that reads
  `self._env.nethack._vardir/xlogfile`, takes the last non-empty line, extracts
  the tab-delimited `death=` field, and applies the data rule above. Call it once
  from `_update_metrics`, only when `info["end_status"]` is terminal
  (`StepStatus != RUNNING`). Wrap all attribute/file access in `try/except →
  None` (the `_vardir` access is a private NLE attribute; guarded by the
  `nle==1.3.0` pin, degrade gracefully otherwise).
- **`arena/trajectory.py`** — `_result()` sets
  `cause_of_death = None if bot_failure else metrics.cause_of_death` (a bot
  failure never produced a death; mirrors the `milestone` zeroing).
- **`arena/run.py`** — `_failed_result` synthesizer sets `cause_of_death=None`.

No change needed in `eval/runner.py` — it already deserializes full
`TrajectoryResult`s from `results.json`.

### B. M1 — local persistence → TUI

- **`harness/loop.py`** — add `causes: dict[str, int] | None = None` to
  `IterationResult`; populate it where the dev `Evidence` already exists, via
  `Counter(r.cause_of_death for r in dev_ev.results if r.cause_of_death)`.
- **`harness/runlog.py`** — `metric_record()` emits `causes`, so it lands in
  `metrics.jsonl` (per-iteration counts; no per-episode rows on disk).
- **`tui/screens/runs.py`** — `_summarize()` sums `causes` across a run's
  `metrics.jsonl` lines into a run-level `Counter`; add it to the summary dict.
- **`tui/screens/home.py`** — new "☠ Causes of Death" panel: aggregate causes
  across the user's local runs, render a Rich `cause → count` table (reuse the
  existing `episode_table` / amber `.panel` idiom), top ~8 rows.

### C. M2 — hub collective

- **`hub/store.py`** — add to `_SCHEMA` (migration-safe: `CREATE TABLE IF NOT
  EXISTS`, a **new table**, so no `atoms` column and no DB wipe):
  ```sql
  CREATE TABLE IF NOT EXISTS deaths (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      solution_digest  TEXT NOT NULL REFERENCES solutions(digest),
      objective_digest TEXT NOT NULL REFERENCES objectives(objective_digest),
      owner            TEXT NOT NULL,
      identity         TEXT NOT NULL,
      seed             INTEGER NOT NULL,
      cause_of_death   TEXT NOT NULL,
      turns            INTEGER NOT NULL,
      created_at       TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(solution_digest, objective_digest, seed)
  );
  ```
  Add `insert_deaths()` (use `INSERT OR IGNORE` for idempotent re-registration).
- **`hub/deaths.py`** (new, sibling of `atoms.py`) — `evidence_to_deaths()`:
  one row per `TrajectoryResult` whose `cause_of_death is not None`.
- **`hub/validate.py`** — `register()` calls `insert_deaths()` right after
  `insert_atoms()`. The raw `cause_of_death` already rides in the POSTed
  `Evidence` (defaulted field), so no client/protocol change.
- **`hub/views/deaths.py`** (new) — `top_causes(store, owner=None, limit=25)`:
  `SELECT cause_of_death, COUNT(*) AS count FROM deaths [WHERE owner=?]
   GROUP BY cause_of_death ORDER BY count DESC LIMIT ?` → `[{cause, count}]`.
- **`hub/api.py`** — `GET /deaths?owner=&limit=` (plain dict/list, matching the
  existing read handlers; no Pydantic response model).
- **`hubclient/client.py`** + **`hubclient/render.py`** — `HubClient.deaths()`
  plus rich + plain renderers.
- **`cli.py`** — `nethackers deaths [--owner X]` wired through
  `emit(..., table=rich_deaths, plain=plain_deaths)`, so `-o json` is free.

## Testing

- **Extraction unit** (`arena/environment.py`): a fixture xlogfile line → correct
  `death=`; the data rule maps `quit`/`escaped`/`ascended`/ascension → `None`;
  missing/empty file → `None`.
- **Contract round-trip**: `TrajectoryResult.to_dict`/`from_dict` preserves
  `cause_of_death`; a legacy dict without the key → `None`.
- **M1**: `metric_record` includes `causes`; `_summarize` sums a two-line
  `metrics.jsonl` correctly; Home panel content test **plus a screenshot look
  check** (render Textual → SVG → PNG) before calling the panel done.
- **M2**: `evidence_to_deaths` drops `None`; `insert_deaths` is idempotent;
  `top_causes` orders by count desc; API test — POST evidence with
  `cause_of_death` → `GET /deaths` returns the expected counts.
- Reuse the spike script (`scratchpad/death_probe.py`) as a manual end-to-end
  smoke against a real NLE episode.

## Risks / notes

- **Private NLE attribute** (`_vardir`): brittle across NLE versions; pinned to
  `1.3.0`, guarded, degrades to `None` (no death recorded) rather than crashing.
- **quit/escaped/truncation** all surface as `end_status == DEATH` with
  `death=quit`; handled by the string filter, not `end_status`.
- **Backward compatibility**: every consumer of old evidence / old
  `metrics.jsonl` sees `cause_of_death`/`causes` absent → `None`/`{}`; no death
  rows written, nothing breaks.
- **Ship order**: M1 is fully local (zero hub/DB risk) and delivers the core
  delight; M2 adds the collective layer behind the same captured field.
