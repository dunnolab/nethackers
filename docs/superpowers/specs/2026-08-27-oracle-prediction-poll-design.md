# The Oracle — community prediction poll — design (v1)

Date: 2026-08-27
Status: proposed (awaiting review)

## Motivation

The whole premise of NetHackers is an open question: *will harnesses / code-as-policies
be the thing that finally wins NetHack, and when?* Nobody knows — that uncertainty is the
point. So we ask the visitor, collect the crowd's forecast, and show it back split by who's
answering. A researcher's timeline next to a NetHack player's *is* the knowing–doing-gap
thesis rendered as a chart.

The section is themed **"The Oracle"** — in NetHack the Oracle sells prophecy for gold; ours
is free, and the crowd is the oracle. It lives on the public site (`GET /`), served by the
hub, in the site's Web-1.0 idiom.

## Prototype status (what's already built)

A full, clickable prototype is **already in `src/nethackers/hub/web/index.html`** — the
`#oracle` section (HTML), its CSS block, and a self-contained `<script>` IIFE — and has been
iterated to sign-off via headless (Playwright) screenshots. It is **client-only**: it runs a
synthetic 500-voter corpus (`genCorpus()`, a seeded LCG) and stores the visitor's own vote in
`localStorage`. There is **no API and no persistence yet**.

Everything the prototype renders — ballot, vote-to-reveal, sorted bars, segment lens, compare
view, the median callout, honest-N line, the oracular reveal passage — **carries over
unchanged**. This spec's real work is the production data path: a votes table, two endpoints,
and swapping the synthetic corpus for a live fetch. The render/aggregation code does not change.

## Decisions locked (from brainstorming)

**Two questions**, both crisp and (someday) resolvable. "Solved" = **first ascension on
held-out seeds** (the milestone Q2 resolves on).

- **Q1 — method:** *"Which approach writes the first ascending program?"* — six options
  (keys): `programs` (LLM-written programs), `rl` (end-to-end deep RL), `llm` (LLM agent, live
  play), `symbolic` (hand-engineered bot), `hybrid` (neuro-symbolic), `never`.
- **Q2 — timeline:** *"When does the first ascension on held-out seeds happen?"* — six buckets:
  `2027`, `2030`, `2035`, `2040`, `after` (after 2040), `never`. The `never` bucket carries the
  will-it-ever axis, so there is no separate confidence question.

**Segmentation** — self-selected, two independent axes:

- **Tribe (role): MULTI-select** — `mlr` (AI/ML researcher), `player` (NetHack player),
  `eng` (software engineer), `enth` (AI enthusiast). A person can be several; segments overlap.
- **NetHack XP: single-select ladder** — `never`, `casual`, `serious`, `ascended`.

Both are optional. The role axis is multi because people wear more than one hat; XP is single
because it's a ladder (you're on one rung). The ascended-vs-never split is the sharpest cut.

**Vote integrity:** open, **one prophecy per browser**, self-reported. No login. This matches
the leaderboard's disclosed-self-reported honesty; gameable by clearing storage, accepted and
labeled.

**Reveal + display rules:**

- **Vote-to-reveal** — the crowd is hidden until you cast (kills anchoring, lifts participation).
- **Re-voting allowed**, last-wins.
- **Both bar charts sorted by percentage**, descending (stable — ties keep canonical order); the
  chart re-ranks per segment so each tribe's favorite sits on top. Q2 also shows a computed
  **median guess**.
- **Selected = indigo bar, others = grey** (the table idiom); `(you)` markers on your picks.
- **Honest N** — total and per-segment counts always shown; a filtered segment with `n < 12`
  gets a "small sample — read with salt" note.
- **Segment lens:** *Everyone* / *By role* / *By NetHack XP*; within an axis, filter to one
  segment or **Compare all** (a card per segment: n, median date, favored approach + %). Role
  cards overlap (Ns don't sum) and say so.
- **Reveal banner** — amber "The Oracle speaks." plus a short oracular **passage** generated
  from the two picks (dry, wry, in the site's voice), not a `key: value` echo.

**Visual/design:** the site's own system — `Times New Roman → Courier New` stacks only, indigo
brand + grey + the terminal amber in the dark banner, the red section glyph on the header. No
decorative Unicode/emoji (`( )` / `(*)` ballot radios, `[ … ]` link idiom, `(you)` markers).

**Placement:** a dedicated section, sidebar item **`[6] The Oracle`**, between Progress and
Contribute (movable).

## Scope

**In (v1):**
1. Persist votes in the hub (`poll_votes` table); one row per browser (upsert).
2. `POST /poll/vote` (record/replace a vote) and `GET /poll` (anonymized rows + total).
3. Swap the prototype's synthetic corpus + local-only storage for the live endpoints; keep all
   render/aggregation code.

**Out (deferred, explicitly):** GitHub-login / "verified" votes; server-side aggregation and
caching (v1 ships raw anonymized rows — see §C); rate-limiting / anti-abuse beyond
one-row-per-browser; a third question or editable question set; renaming "AI enthusiast"; an
admin/export view; pre-seeding the poll (it launches empty and fills as people vote).

## Data model

**Frozen key vocabularies** (the server validates against these; the client owns the labels).
Both copies are small and must agree; a mismatch fails safe (an unknown key → `400` on POST):

```
METHOD   = {programs, rl, llm, symbolic, hybrid, never}
TIMELINE = {2027, 2030, 2035, 2040, after, never}
ROLE     = {mlr, player, eng, enth}        # multi-select subset
XP       = {never, casual, serious, ascended} | null   # single
```

**A vote** (the unit the client sends and the row we store):

```
voter_id : str   # browser-generated UUID, one prophecy per browser
method   : METHOD key
timeline : TIMELINE key
roles    : list of ROLE keys (0–4, deduped)
xp       : XP key or null
```

## Design

### A. The section (`hub/web/index.html`) — already built

The `#oracle` section, its CSS, and the Oracle `<script>` IIFE are in place from the prototype
and stay. The section is static HTML + self-contained client JS; it is **decoupled from the
page's `boot()`** — the Oracle fetches `/poll` itself, so a poll outage never affects the
leaderboard/frontier/progress. Production changes are confined to the IIFE's data source (§D).

### B. Persistence (`hub/store.py`)

Add to `_SCHEMA` — **a new table, `CREATE TABLE IF NOT EXISTS`**, so it is migration-safe:
additive, no `atoms` change, **no DB wipe** (same pattern as the `deaths` table):

```sql
CREATE TABLE IF NOT EXISTS poll_votes (
    voter_id   TEXT PRIMARY KEY,           -- one prophecy per browser
    method     TEXT NOT NULL,
    timeline   TEXT NOT NULL,
    roles      TEXT NOT NULL DEFAULT '[]', -- JSON array of role keys
    xp         TEXT,                        -- nullable
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Two methods:
- `upsert_poll_vote(voter_id, *, method, timeline, roles, xp)` — `INSERT … ON CONFLICT(voter_id)
  DO UPDATE SET method=…, timeline=…, roles=…, xp=…, updated_at=datetime('now')`. Keeps
  `created_at`. This is what makes one-per-browser + re-vote work. `roles` stored as
  `json.dumps(sorted(set(roles)))`.
- `iter_poll_votes()` → `list[dict]` of `{method, timeline, roles (parsed list), xp}` over all
  rows. No `voter_id`, no timestamps — the read path is anonymized.

### C. API (`hub/api.py`)

Two handlers, closures over `store`, matching the existing read/write shape (plain dict/list
responses; a Pydantic body model for the POST, like `RegisterRequest`).

```
class PollVoteRequest(BaseModel):
    voter_id: str
    method: str
    timeline: str
    roles: list[str] = []
    xp: str | None = None
```

- **`POST /poll/vote`** — validate: `voter_id` non-empty and `≤ 64` chars; `method ∈ METHOD`;
  `timeline ∈ TIMELINE`; `xp ∈ XP or None`; `roles ⊆ ROLE` (dedup, cap 4). Any violation →
  `400`. On success, `store.upsert_poll_vote(...)` then return the **same body as `GET /poll`**
  (the fresh aggregate incl. this vote) so the client reveals without a second round-trip.
- **`GET /poll`** — `{"votes": iter_poll_votes(), "total": len(votes)}`. Anonymized rows only.

The frozen vocabularies live in one small module, **`hub/poll.py`** (`METHODS`, `TIMELINES`,
`ROLES`, `XPS` as `frozenset`s + a `validate_vote()` helper the handler calls), so the server
has a single source of truth.

**Why raw rows, not server-side aggregation (v1):** the validated client already computes every
distribution, filter, median, small-sample flag and compare-card from a flat list of votes
(the synthetic corpus). Returning anonymized rows lets the client set `CORPUS ← votes` and
**change nothing else**. Rows are non-identifying (four category keys). The cost is response
size (~50 bytes/row); fine into the low thousands. **Deferred:** past ~5k votes, move
aggregation into a `hub/views/poll.py` (`GET /poll` returns overall + per-segment + compare
distributions) — a self-contained future change behind the same endpoint.

### D. Client wiring (the swap, in the Oracle IIFE)

Replace the synthetic data + local-only storage with the live endpoints; keep `barChart`,
`renderCharts`, `renderCompare`, `setLens`, the sort, the reveal passage, all of it.

- **`voter_id`:** on first use, `crypto.randomUUID()`; persist in `localStorage['nh-oracle-voter']`.
- **`CORPUS`:** now `let CORPUS = []`, filled from `GET /poll`. Delete `genCorpus()` and the
  synthetic weight tables, and delete the `sessionVotes` overlay (the server's rows already
  include the visitor's own vote after they POST).
- **On load:** if `localStorage['nh-oracle-vote']` holds a prior cast → guarded `GET /poll` →
  `CORPUS = data.votes` → reveal. Otherwise show the ballot and **fetch nothing** — the crowd is
  never shipped to someone who hasn't voted (stronger vote-to-reveal; no bandwidth for
  non-voters).
- **On cast:** `POST /poll/vote {voter_id, method, timeline, roles, xp}` → response aggregate
  (§C) → `CORPUS = data.votes`; `writeVote(localStorage)`; `reveal(vote)`. No separate `GET`.
- **Degradation** (the page's ethos — never a traceback, always a friendly state): `GET`/`POST`
  failure → keep the ballot usable; on a failed reveal, show *"the Oracle is silent — try
  again"* and echo the visitor's own stored pick without a crowd. A poll outage leaves the rest
  of the page untouched (decoupled fetch).

### E. Aggregation & segmentation (client, unchanged — now over real rows)

- **Everyone:** distribution over all rows, per question.
- **By role (multi):** subset = rows where the role key ∈ `roles`; segments **overlap** (Ns sum
  past the total, and the compare caption says so).
- **By XP (single):** subset = rows with that `xp`.
- **Median timeline:** ordinal median over a subset's `timeline` values.
- **Small sample:** filtered `n < 12` → display warning (a display heuristic, not a data filter).
- **Compare:** per segment → `{n, median date, top method + %}`.

## Testing

Following the hub's test conventions (`tests/hub/test_*.py`, plain stdlib + FastAPI TestClient):

- **Store** (`test_store.py`): `upsert_poll_vote` inserts, then the **same `voter_id` updates in
  place** (one row, new values, `created_at` preserved); `roles` JSON round-trips (order-normal-
  ized, deduped); `iter_poll_votes` returns anonymized dicts with parsed `roles`.
- **API** (`test_poll.py`, new): valid POST persists and returns the aggregate; **each**
  validation rejection is a `400` (unknown method / timeline / xp / role key; empty or
  over-long `voter_id`; `> 4` roles); a second POST with the same `voter_id` replaces (total
  unchanged, values updated); `GET /poll` returns `{votes, total}` with the right count and no
  `voter_id`/timestamps.
- **Vocabulary guard:** `hub/poll.py`'s key sets match the client's — a test parses the
  `METHOD`/`TIME`/`ROLE`/`XP` key lists out of `web/index.html` and asserts equality with the
  Python `frozenset`s, so the two sources of truth can't silently drift.
- **Client:** the repo has no JS unit harness; the interactive behavior is verified the way the
  prototype was — a headless (Playwright) pass over ballot → cast → reveal → sort → lens →
  compare, eyeballed against screenshots — before calling it done.

## Risks / notes

- **Additive, migration-safe:** `poll_votes` is a new `CREATE TABLE IF NOT EXISTS`; it appears on
  the next hub startup with no wipe. Prod deploy uses the documented recipe (git-archive → build
  on the VM → `--force-recreate`); nothing touches existing tables.
- **Gameability:** open voting, one row per browser (clearable, multi-browser bypassable).
  Disclosed as self-reported — consistent with the boards. One-row-per-browser stops casual
  double-voting; determined stuffing is accepted for v1.
- **Vocab drift:** client labels vs server keys must agree; both are small frozen lists, guarded
  by the parity test above, and a stray key fails safe (`400`).
- **Scale ceiling:** raw-rows `GET /poll` grows with N; the server-aggregation fallback (§C) is
  the deferred fix, behind the same endpoint, when volume warrants.
- **Empty launch:** the poll ships with no votes; vote-to-reveal means the first voter sees
  `n=1` (honest), and the `n < 12` note covers the early low-N window. No seeding.
- **Privacy:** stored rows are four anonymous category keys + an opaque browser UUID; the read
  path drops the UUID and timestamps. No PII, nothing to leak.
