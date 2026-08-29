# Hub API coherence redesign (design)

**Status:** validated by live iteration with the user (2026-08-29); design phase
complete, ready for an implementation plan. This is a **decision record**, not a
design exploration — the five decisions below were settled one fork at a time
against a living architecture map, and each is locked. Living reference: the
**NetHackers Hub Atlas** Artifact
(https://claude.ai/code/artifact/477cd0b9-f115-4971-b84c-629b000269f2), kept in
sync with this spec.

**Trigger:** a `404` on the TUI Frontier "Program" tab — a link-registered
program's digest `github.com/owner/repo@commit` (slashes) never matched the
`/solutions/{digest}/frontier` route (default `{digest}` converter spans one path
segment). The interim fix (`{digest:path}` + route reorder, branch
`vkurenkov/fix-another-bug-image`, commit `34dcd0f`) unblocks that one route and
is **not deployed**; this redesign removes the whole bug class and the naming
incoherence behind it, so the interim fix is superseded rather than shipped.

**Scope of change:** `hub/api.py` (routes), `hub/store.py` (additive column +
methods), `hub/views/*` (row builders), `hubclient/*` (client + adapters +
renderers), `tui/*`, `cli.py`, `web/index.html`. **Hard cutover** — no
deprecation layer (see Out of scope).

## What it is

The hub stores one substrate: the **atoms cube** — one row per
`(program, identity, seed)` episode, with measures `progression`, `milestone`,
`ascended`, `status`, `turns`, `steps`. Every "ranking" endpoint is a
**projection** of that cube:

| endpoint | projection |
|---|---|
| `/board` | collapse identity+seed → rank programs |
| `/elites` | best program on each identity |
| `/solutions/{d}/frontier` | fix one program, spread across identities |
| `/hackers` | group by owner |
| `/attainment` | first program to reach each (identity, milestone) |
| `/progress` · `/stats` | over time · global counts |
| `/baseline` | the same, over AutoAscend's isolated atoms |

Today each projection is a hand-built endpoint with its own name and row shape,
and the vocabulary collides — `frontier` (4 referents, 2 meanings), `digest` (a
link masquerading as a hash), `objective` (4 meanings), and `/board` (three row
shapes behind XOR params). The redesign makes the projection model explicit and
the names honest: **named view-resources over the cube with one uniform envelope
and vocabulary, opaque program ids, and a raw `GET /atoms` export** as the escape
hatch for every other slice.

## Validated design (do not redesign — this is what the user approved)

### 1. Shape — named resources + raw export (NOT a query DSL, NOT GraphQL)

Keep purpose-built, named endpoints; do **not** build a composable
`/query?group_by=…&aggregate=…` endpoint, and do **not** adopt GraphQL. Reasons
(all decisive for this system):

- The projections are **bespoke, not generic group-bys**: `firsts`/`coverage`
  read the ratchet tables, `elites` is per-identity argmax with tie rules, the
  board's median needs Python (`statistics.median`; SQLite has none). A DSL
  becomes presets-with-special-cases behind it — you build the named resources
  anyway, plus a second surface.
- A query endpoint is an **unbounded public contract** (Hyrum's law over every
  expressible query), the opposite of "make it make sense".
- The **official ranking is the product** — a query endpoint invites clients to
  compute a "board" that disagrees with the canonical one, killing comparability.

The escape hatch for any other slice is **`GET /atoms`** — publish the raw rows,
let power users compute what they want (the Hugging Face-leaderboard move; cheap,
the DB is tiny, and it is exactly the transparency a research leaderboard wants).

Governing principle for the whole surface: **paths select the row shape; params
only select a subset** (filter/scope/sort), never change the schema.

### 2. Compatibility — hard cutover (lab-scoped)

The board is lab-internal for now and the user can coordinate directly with
submitters. So: **no deprecation apparatus** (no `Deprecation`/`Sunset` headers,
no `410` windows, no `/healthz` version field). Build the new API, migrate our
own consumers (TUI / CLI / web / harness) in lockstep, **delete the old read
paths outright**, and give the lab a heads-up. `POST /register` still gains `id`
**additively** (near-free future-proofing). The full versioning story is
**deferred until the board actually goes public** (see Out of scope) — YAGNI now.

### 3. Identifier — opaque `prog_` id

```
id = "prog_" + sha256(f"{repo}@{commit}")[:32]          # 128 bits
```

- **Opaque** — an external repo link is never the public key (avoids Hyrum's-law
  coupling; the user: "external ids are strange"). The link becomes a
  `reference {repo, commit}` **attribute**, surfaced on program-bearing rows.
- **128-bit (32 hex).** First expected collision ≈ 10¹⁹ programs (birthday
  bound), past any physical scale — sized so it is never revisited even if the
  board goes viral (the 48-bit first cut was rejected for exactly this).
- **Deterministic**, not random: registration stays idempotent (same
  `repo@commit` → same id), and every existing row migrates **by pure function**
  — the stored `digest` already *is* `f"{repo}@{commit}"`, so
  `id = "prog_" + sha256(digest)[:32]`. No lookup table.
- **Identity = `repo@commit`.** `root` and `entrypoint` are **dropped** from the
  manifest — the arena hard-requires `bot.py` at the repo root
  (`arena/sandbox.py`), so neither was ever consumed by any run path (both were
  validated + stored + shown, but always `.` / `bot.py`). `validate.py` stops
  requiring them; the now-vestigial `solutions.root`/`entrypoint` columns can stay
  (unused) or be dropped. With no `root`, each `repo@commit` is **unambiguously one
  program**.

### 4. Envelope — one list shape

Every **collection** read returns an envelope, never a bare array, so responses
grow additively:

```jsonc
{ "generated_at": "2026-08-29T…Z",
  "scope": "generalist", "tier": "self-reported",   // only the applicable context keys
  "rows": [ … ] }
```

`scope`/`tier`/`identity` appear only where they apply. **Single-resource** reads
(`GET /programs/{id}`, `/stats`) return the resource object directly, not wrapped.

`generated_at` is the hub's authoritative **as-of** — the response reflects every
atom committed up to that instant. Kept (not cargo-culted): the hub is written
concurrently by verifiers / distributed updaters, and **agent consumers have no
reliable request clock** and their HTTP tooling routinely drops response headers,
so the `Date` header isn't reachable in practice — an in-band freshness marker is
real information here.

### 5. Naming sweep

| old | new |
|---|---|
| `solution` / `solution_digest` (surface) | `program` / `program_id` |
| `digest` (a repo link) | `id` (opaque `prog_…`) + `reference {repo, commit}` |
| `frontier` (UI label — tab, CLI cmd) | **kept** — the product name, unchanged |
| `/solutions/{d}/frontier` (API path) | `/programs/{id}/identities` |
| `objective=` used as a scope | `scope=` param (code already: `resolve_scope`) |
| `Objective` (run-config dataclass) | `EpisodeConfig` |
| `objective` (grading `ObjectiveSpec`) | `objective` — kept, the only true one |
| `attainment` (endpoint + `views/`) | **`achievements`** — `/achievements[/milestones\|/coverage\|/firsts]` (DB tables stay `attainment`, storage ≠ surface) |
| bare `[ … ]` array | `{ generated_at, scope?, tier?, rows:[…] }` |

**`Frontier` stays as the user-facing label — everywhere** (the TUI tab, the CLI
command, any displayed text). It is the product name for this view and its
**Universe** and **Program** regimes, and the user asked explicitly to keep it.
What was actually overloaded is the *API/internal* reuse of the word for a
**narrower** meaning than the tab (the tab = the whole view; the old
`/…/frontier` endpoint = only one program's per-identity spread). That is fixed by
giving the **API** precise resource names — the collective regime is
`GET /elites`, the per-program regime is `GET /programs/{id}/identities` — so the
**UI keeps "Frontier" while the API carries precise names** (the two need not
match). The `hubclient/frontier.py` adapter and `render_frontier_grid` name the
retained Frontier view and **stay**. `objective` survives only on `ObjectiveSpec`.

### The endpoint surface (today → proposed)

| today | proposed | notes |
|---|---|---|
| `/search` | `GET /programs` | list, enveloped |
| `/solutions/{digest}` | `GET /programs/{id}` | single object |
| `/solutions/{digest}/frontier` | `GET /programs/{id}/identities` | one program × identities |
| `/board?objective=` (3 shapes) | `GET /board?scope=` | **one** row schema |
| `/board?metric=coverage` | `GET /achievements/coverage` | achievements projection |
| `/board?metric=firsts` | `GET /achievements/firsts` | achievements projection |
| `/elites?objective=` | `GET /elites?scope=` | best per identity |
| `/hackers?objective=` | `GET /hackers?scope=` | by owner; `scope` now spans role/race/align/gender facets |
| `/hackers/random` | `GET /hackers/random` | **kept** (website-wall sampler); now enveloped |
| — | `GET /hackers/leaders?by=<facet>` | **new** — best hacker per role / race / align / gender |
| `/attainment` | `GET /achievements/milestones?identity=` | first-to-reach ledger |
| `/progress` · `/stats` · `/baseline` | same paths, enveloped where a list | |
| `/objectives` | `GET /objectives` | catalog listing, kept |
| — | `GET /atoms?program=&identity=&tier=` | **new** raw export |
| `/objectives/{name}/batch` | **retired → deleted** | zero consumers (eval reads the in-process catalog) |
| `POST /register` | `POST /register` | same path; response **gains `id`**, accepts old+new keys |

`/` (dashboard HTML), `/healthz`, `/poll`, `/poll/vote`, `/dictionary.mp3` are
unchanged.

## Architecture

### Data layer (`hub/store.py`) — additive, no atoms re-key

- `ALTER TABLE solutions ADD COLUMN program_id TEXT` (nullable), **backfill**
  `program_id = "prog_" + sha256(digest)[:32]` for every row, then
  `CREATE UNIQUE INDEX` on it. Idempotent migration in `init_schema()`, guarded
  like the existing `_migrate_*` helpers (no-op once the column exists).
- `atoms.solution_digest` **stays** the internal key (FK to `solutions.digest`);
  the UNIQUE(solution_digest, identity, seed) index and all atom rows are
  untouched. `program_id` ↔ `digest` translation happens at the API boundary via
  the indexed `solutions.program_id` column.
- New/renamed `Store` helpers: resolve `program_id → solution row` (and reverse),
  and a filtered `iter_atoms` pass-through for `GET /atoms`. `random_owners`
  (from the merged v0.16.0) is unchanged.
- **Write model — register is the only writer; reads never mutate.**
  `POST /register` writes `solutions` + `atoms` + `lineage` + the additive
  `attainment`/`attainment_holders` ratchet (`update_attainment`:
  INSERT-OR-IGNORE + ON-CONFLICT, no DELETE). **`elite_pool` and
  `recompute_elites` are deleted:** `/elites` becomes a **live** top-k-per-identity
  query over `atoms` (window function), so register no longer does an
  O(all-atoms) DELETE-and-rebuild of a materialized elite table. Every GET
  (`/board`, `/elites`, `/hackers`, `/achievements`, `/programs`, `/atoms`,
  `/progress`, `/stats`, `/baseline`) is then a pure read — consistent with the
  atoms-cube framing (`atoms` is the substrate, the rest are projections).
  `attainment` stays materialized: it is an additive ratchet encoding the
  temporal "first-to-reach" fact, cheap and correct to write at register time.

### API layer (`hub/api.py`, `hub/views/*`)

- A small **envelope helper** wraps every collection response
  (`{generated_at, **context, rows}`); handlers build `rows` and pass context.
- **`/board?scope=`** collapses today's three shapes into one builder with a
  single row schema (below). `coverage`/`firsts` move out to
  `views/achievements.py` as `/achievements/coverage` and `/achievements/firsts` —
  they are projections of the achievements record (the internal
  `attainment`/`attainment_holders` tables, kept as-is — storage ≠ surface), not
  score boards.
- `views/boards.py`, `views/elites.py`, `views/hackers.py`, `views/solution.py`
  (→ program) emit `program_id` + `reference` instead of `solution_digest`;
  `resolve_scope` is the `scope=` resolver — renamed param, **extended** to
  `race:`/`align:`/`gender:` facet scopes (role becomes explicit `role:`); a new
  `views/hackers.py` `leaders(by)` builds the per-facet-value champions for
  `/hackers/leaders`.
- Program-id minting lives in one place (used by register + backfill).
- Route converters: paths are opaque-id (slash-free), so the `{digest:path}`
  hack and its route-ordering trap disappear.

### Consumers (migrated in lockstep — hard cutover)

- **`hubclient/`**: `client.py` methods repoint to the new endpoints;
  `frontier.py` and `render_frontier_grid` **keep their names** (they name the
  retained Frontier view) — their bodies just fetch `/elites` and
  `/programs/{id}/identities`; `_short_digest` → id-aware (ids are already short
  and slash-free).
- **TUI** (`tui/screens/hub.py`, `home.py`): the **"Frontier" tab keeps its
  name**; its Universe / Program regimes read `/elites?scope=` and
  `/programs/{id}/identities`; `home.py` program count via `/programs`.
- **CLI** (`cli.py`): the **`frontier` command keeps its name**; other commands
  repoint to the new endpoints (`elites`, `show` → program lookup); `submit`/
  `register` read the new `id` from the response; `pull` unchanged (still takes
  `repo@commit`, or resolves `id → reference` via `GET /programs/{id}`). Any
  remaining user-facing command spellings to be confirmed in the plan.
- **web `index.html`**: repoint every `fetch` (stats, baseline, objectives,
  hackers, board, elites, progress, solutions→programs, poll) at the new paths +
  envelope (`.rows`); `/hackers/random` is now enveloped too (web reads `.rows`).
- **harness** (`harness/launch.py`): register response now carries `id`; store it
  where the digest was used for display/lineage.

### `POST /register` — additive

Path and request envelope unchanged. The **response** gains `id` next to the
existing fields (the old `solution_digest`/reference stay during the cutover so a
mid-flight submit never breaks); requests accept both old and new key spellings.
This is the one endpoint we evolve additively rather than cutting over.

## Endpoint reference (response schemas)

- `GET /programs` → `{generated_at, rows:[{id, owner, reference:{repo, commit}, registered_at}]}`
- `GET /programs/{id}` → `{id, owner, reference:{repo, commit}, registered_at}` (404 → `{detail}`)
- `GET /programs/{id}/identities` → `{generated_at, program_id, rows:[{identity, progression, episodes}]}`
- **`scope`** (on `/board`, `/elites`, `/hackers`) ∈ `{ generalist, role:<r>, race:<r>, align:<a>, gender:<g>, <identity> }` — the four facets decompose each `role-race-align-gender` identity (facet sets already in `hub/objectives.py`). rank 1 of a facet scope = the best program (or hacker) for that value.
- `GET /board?scope=&tier=` → `{generated_at, scope, tier, rows:[{rank, program_id, owner, reference:{repo,commit}, coverage, identities_total, ascensions, mean_progression, median_progression, deepest}]}` (identity scope: `coverage=identities_total=1`)
- `GET /elites?scope=&tier=` → `{generated_at, scope, tier, rows:[{rank, identity, program_id, owner, score}]}`
- `GET /hackers?scope=&tier=` → `{generated_at, scope, tier, rows:[{rank, owner, coverage, identities_total, mean_progression}]}`
- `GET /hackers/random?n=` → `{generated_at, n, rows:[<handle strings>]}` (sampler; the web wall reads `.rows`)
- `GET /hackers/leaders?by=role|race|align|gender` → `{generated_at, by, rows:[{value, owner, score, coverage}]}` — best hacker per value of the facet (the "for each role/race/alignment/gender" view)
- `GET /achievements/milestones?identity=` → `{generated_at, rows:[{identity, milestone, first_program_id, first_owner, first_at}]}`
- `GET /achievements/coverage` → `{generated_at, rows:[{rank, program_id, owner, cells_held}]}`
- `GET /achievements/firsts` → `{generated_at, rows:[{rank, program_id, owner, firsts}]}`
- `GET /atoms?program=&identity=&tier=` → `{generated_at, rows:[<atom>]}` (raw atom rows; `program` is a `program_id`)
- `GET /progress?scope=&tier=`, `GET /stats`, `GET /baseline` → as today, enveloped where a list
- `GET /objectives` → catalog listing (unchanged)
- `POST /register` → `{id, …existing fields}`

## Migration / rollout (hard cutover)

1. **Schema**: additive `program_id` column + backfill + unique index (idempotent
   in `init_schema`). Ships in the same image; the prod DB at
   `/srv/nethackers/data` is preserved (no wipe).
2. **API**: add the new resources; keep `POST /register` additive; **delete** the
   old read routes and `/objectives/{name}/batch` in the same change.
3. **Consumers**: update hubclient, TUI, CLI, web, harness in lockstep so the
   released package + deployed hub match.
4. **Ship**: PyPI release (CLI/TUI) + hub redeploy (release-tag CD, by digest,
   auto-rollback — see `hub-deploy-mechanism`). Coordinate the lab heads-up before
   the flip; a lagged CLI will hit deleted read paths until it upgrades (accepted,
   lab-scoped).

## Testing

The hub is TestClient-testable; every resource + the migration are unit-covered.

- **Per resource** (`tests/hub/test_api.py` + `views` tests): correct envelope
  (`generated_at`, applicable context, `rows`), correct row schema, `scope`/`tier`
  handling, and a **slash-free `prog_` id round-trips** through
  `GET /programs/{id}` and `/programs/{id}/identities` (the original bug's
  regression, now structural — ids can't contain slashes).
- **`/board?scope=`**: one row schema across identity / role / generalist scopes
  (identity scope → `coverage=identities_total=1`); the old XOR-params 400 and the
  three duck-typed shapes are gone.
- **`/achievements/coverage` + `/achievements/firsts`**: correct rows; confirm the old
  `/board?metric=` forms are deleted (404).
- **Identifier**: `id` is deterministic (same `repo@commit` → same id), and the
  backfill is a **pure function** of the stored `digest` (property test over
  existing-shape rows).
- **`/atoms`**: filters by `program`/`identity`/`tier`; excludes `baseline_atoms`.
- **`POST /register`**: response carries `id`; a request with old-key evidence
  still succeeds (additive).
- **Consumers**: `tests/test_hubclient.py`, `tests/test_tui_hub_views.py`,
  `tests/test_render_frontier.py` (→ renamed), and the jsdom web `wire.test.mjs`
  updated to the new paths/envelope. TUI look verified by SVG render (per
  `verify-tui-looks-with-screenshots`).
- **End-to-end against the local stack** (not just unit/TestClient tests): bring
  the real hub up and exercise it — `make hub` (stub + fixtures) to drive the TUI
  **Frontier** tab, hit the new read endpoints (`/programs`, `/board?scope=`,
  `/elites`, `/programs/{id}/identities`, `/atoms`), and confirm the additive
  `program_id` migration runs against a real sqlite volume (`make hub-reset` to
  re-migrate from empty); `make up HUB_AUTH=github` to validate `register → /board`
  end-to-end (the `POST /register` response carries `id`; the board shows the
  program). See `docs/local-stack.md`.

## Out of scope / parked

- **The deprecation apparatus** — `Deprecation`/`Sunset` headers (RFC 9745/8594),
  `410` windows, a `/healthz` `api:{version,sunset}` field, a `/v2` prefix. Added
  **only when the board goes public**; a hard cutover is correct while lab-scoped.
- **Renaming the `solutions` table** to `programs` internally — storage ≠ surface;
  keep the table name to minimize the migration (optional later).
- **`GET /atoms` pagination / rich querying** beyond simple filters.
- **Configurable entrypoint / subdirectory layout** — the arena hard-codes
  `bot.py` at the repo root, so `entrypoint` is dropped alongside `root`; if
  per-bot entrypoints or monorepo (subdirectory) layouts are ever needed,
  reintroduce a manifest field then. Dropping `root` also removes the
  monorepo-identity ambiguity — each `repo@commit` is one program.
- **GraphQL and the composable query endpoint** — rejected in §1, not revisited.
- The interim `{digest:path}` fix — superseded by the opaque id; not deployed.
