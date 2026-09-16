# Markdown for agents — content negotiation on the website

Status: design, 2026-09-16.

Serve a compact, agent-facing markdown brief to clients that ask for
`text/markdown`, so that a coding agent handed the hub's URL can answer "what
is this", "install it for me" and "who's winning" from the site itself instead
of reconstructing the answer from five other systems.

## 1. Problem

`GET /` returns 206,052 bytes of HTML carrying 11,421 characters of prose. The
live numbers are not in it: the Frontier grid, the keepers and breakthrough
tables, the odometer counters and "last updated" are all populated after load
by `jget` against the JSON API. A fetcher does not run JavaScript, so it sees
the loading placeholders — `Loading frontier keepers…`, `last updated: —` —
and never the page's own (carefully written) empty states.

Seven agent runs against production on 2026-09-16, across five scenarios, each
standing in for a user who had just pasted the link. Four of the five are in
scope here; the fifth ("what is nethackers?", with no link) is a discoverability
problem and is parked (§11).

| Scenario | Outcome |
|---|---|
| bare link | concluded *"the site is serving an empty database"*; drafted SSH diagnostics for a hub incident |
| "install this for me" | site contributed **zero** commands; agent used PyPI, `gh`, astral.sh, the Homebrew API and GHCR, and reconstructed a third of its plan from convention |
| "what is this?" | correct on the mission; closed on *"a leaderboard with nothing on it and a source link that doesn't resolve"* |
| "who's winning?" | *"Nobody is winning"* — true, but reported as suspected data loss |

The zeros are correct and intended: genesis ran for the amd64 reset
(`2026-09-14-amd64-reference-reset-design.md`), archiving the public tables to
`*_v1`. Nothing a fetcher can reach says so, so "deliberately reset" and
"broken" are indistinguishable from outside.

Three further measurements frame the fix:

- **The header is already arriving.** Claude Code sends `Accept: text/markdown,
  text/html, */*` with UA `Claude-User (claude-code/2.1.271;
  +https://support.anthropic.com/)` — captured twice today against independent
  echo services. The hub ignores it: `text/markdown`, `text/plain` and
  `application/json` all receive the same 206 KB of `text/html`, and
  `/index.md` and `/llms.txt` are 404.
- **`/openapi.json` and `/docs` are already public** — 29 routes — and not one
  of the seven agents found them. Two independently guessed `/summary` and got
  a 404.
- **`HEAD /` returns 405.** The route is GET-only.

## 2. Mental model (read first)

The website has two audiences with incompatible needs and one representation.
A human wants the ASCII masthead, the dungeon type and the argument. An agent
wants three declarative answers and a way to check them.

This design does not restyle the page or move its data server-side. It adds a
**second representation of the same resource**, selected by HTTP content
negotiation, and treats the two as independent documents that happen to
describe the same project. The markdown is not a transcription of the HTML —
it is written for a reader that arrived with a question and a token budget.

Two consequences follow, and both are load-bearing:

1. **The brief carries what the page cannot.** Live numbers (the page hides
   them behind JS), install commands (the page has none) and the reset
   explanation (the page never mentions it).
2. **The brief must never be the only true copy of anything.** It is a derived
   view: prose condensed from `README.md`, numbers read from the store on every
   request. Drift is the failure mode this design spends the most effort on.

## 3. Who this reaches, and who it does not

Measured across two independent header-capture projects, plus source reads of
the open-source clients:

| Advertises `text/markdown` | Does not |
|---|---|
| Claude Code, Cursor, GitHub Copilot (CLI + Chat, since mid-2026), OpenCode, Gemini CLI (direct mode) | ChatGPT-User **and therefore Codex**, ChatGPT agent, the Claude.ai web app, Perplexity, the Gemini app, `curl`, Windsurf |

Negotiation therefore serves **coding agents**. Chat products continue to
receive HTML. This is stated here so that nobody later reads a flat scenario-1
result as a regression: the header fix cannot reach a client that does not send
the header. The one lever that reaches the OpenAI path is the `<link
rel="alternate">` hint of D5, which its fetcher is reported to follow with a
second request for the `.md` sibling.

## 4. Decisions ledger

- **D1. Negotiate on `Accept`, never on `User-Agent`.** Rejected: serving
  markdown to known AI user agents. It shows crawlers something browsers do not
  see, the list rots, and it would not work anyway — Perplexity is documented
  fetching with generic browser UAs. `Accept` is the client stating what it can
  read, which is the question we actually have.
- **D2. Markdown iff `text/markdown` is explicitly listed with q>0 and no
  listed media range carries a strictly higher q. `*/*` alone is HTML. Never
  406.** RFC 9110 makes Claude Code's `text/markdown, text/html, */*` a
  three-way tie at q=1 and leaves the choice to the server; every one of the six
  production implementations measured (Anthropic/Mintlify, Vercel, Cloudflare,
  Stripe, Expo, Read the Docs) resolves that tie to markdown and bare `*/*` to
  HTML. Stripe additionally ignores q-values entirely and returns markdown for
  `text/markdown;q=0.5, text/html`; that is a bug, and is not copied. Starlette
  ships no Accept parser, so this is ours to write.
- **D3. The brief is its own document, not a rendering of `index.html`.**
  Chosen so that scenarios 2 and 5 can pass at all: a faithful mirror would
  inherit the page's two gaps (no install instructions, no data outside JS).
- **D4. The negotiated response is `text/markdown; charset=utf-8`; the explicit
  `.md` and `llms.txt` URLs are `text/plain; charset=utf-8`.** RFC 7763 makes
  charset required on `text/markdown`. The split exists because ChatGPT's
  reader MIME-gates and rejects a `text/markdown` body as non-renderable
  (re-observed in a live issue thread on 2026-09-16, where the fix was switching
  to `text/plain`), and Firefox downloads `text/markdown` rather than displaying
  it. A client that negotiated for markdown has proven it can read markdown; a
  client following a link has proven nothing.
- **D5. `index.html` gains `<link rel="alternate" type="text/markdown"
  href="/index.md">`.** One line, shipped by every site in the survey, and the
  only mechanism that reaches the ChatGPT/Codex fetch path.
- **D6. Corrections come first, before the description.** Stripe and Expo both
  open their agent markdown with what models get wrong; the supporting evidence
  is that relevant facts are retrieved best at the beginning or end of a context
  and that Claude Code truncates a page and passes it through a small extraction
  model. Our three corrections are each a mistake a real agent made today: the
  board was reset and zeros are not failure; scoring is amd64 and a native arm64
  score is refused; the repo is private, so install from PyPI.
- **D7. The reset is stated from the presence of the archive tables, not from a
  stored timestamp.** `genesis` records no time and has already run in
  production, so any timestamp column would be `NULL` exactly where it matters.
  `solutions_v1` existing is a fact the store can answer today. The brief says
  an earlier epoch is archived and gives the arena major; it never invents a
  date. Rejected: adding a write to `genesis` (retroactively useless), and
  deriving a date from `MAX(created_at)` on `atoms_v1` (that is the last
  registration before the reset, not the reset).
- **D8. Numbers are read per request; no cache.** `read_stats` is six `COUNT`s
  and `read_baseline` is one table read — the same cost profile as the existing
  `index()` handler, which already reads a file and the package version on every
  request.
- **D9. `/llms.txt` serves the same bytes as `/index.md`.** The convention is a
  link index, but this site is one page: the brief already *is* the index, and
  two documents would be two things to keep true. Honest expectation, recorded
  so nobody over-invests: Ahrefs' May 2026 logs over 137,210 domains found 97%
  of published `llms.txt` files were never fetched at all, and AI bots never
  probe for a missing one. It ships because Claude Code — the client this design
  targets — is the second-most-frequent fetcher of the ones that are read, behind
only GPTBot among named AI tools.
- **D10. `HEAD /` returns 200.** Some fetchers probe before they get.
- **D11. The rendered brief is capped at 4 KB, enforced by a test.** Comparable
  landing-page briefs measured today: Vercel 1,582 B, Cloudflare 2,998 B,
  Mintlify 7,232 B, Expo 13,788 B.

## 5. Components

### 5.1 `src/nethackers/hub/negotiate.py` (new)

One public function, no FastAPI imports, no store access:

```python
def prefers_markdown(accept: str | None) -> bool:
    """True iff the client explicitly asked for markdown and nothing it listed
    outranks it. A missing or `*/*` Accept is False: RFC 9110 lets us answer
    those with anything, and the page is what a browser and a curl user want."""
```

Parses media ranges with parameters, reads `q` (default 1.0 per RFC 9110
§12.5.1), drops `q=0` entries, and compares the q of an explicit `text/markdown`
against the highest q among the other explicitly listed ranges. `text/*` and
`*/*` are wildcards, not an explicit request: they never select markdown.

Kept separate from `api.py` because it is pure string handling with a large
truth table, and that is worth testing without a TestClient.

### 5.2 `src/nethackers/hub/web/brief.md` (new, package data)

The brief's prose, with `{{placeholder}}` slots for every number — the same
convention `index.html` already uses for `{{version}}`. Sits beside
`index.html` so the two web assets live together.

`pyproject.toml` gains a `force-include` entry mirroring the existing
`index.html` one. See I6 and §7 for why this is a real failure mode and how it
is guarded.

### 5.3 `src/nethackers/hub/views/brief.py` (new)

```python
def render_brief(store: Store, *, tier: str, epoch: Epoch | None, version: str) -> str
```

Reads `read_stats(store)` and `read_baseline(store, tier=...)` for both tiers,
asks the store whether the archive tables are present, substitutes, and returns
the document. A view module like its neighbours: pure reads, no writes, no
HTTP.

The verified read is guarded rather than fatal. `source_for` raises
`VerificationUnavailable` on a hub with no verifier configured, and `api.py`
turns that into a 503 for the tier-bearing endpoints. The brief must not 503:
it catches that case and omits the Private Dungeons floor, leaving the rest of
the document intact. A hub without a verifier still has a brief.

One new store helper, `Store.has_archive()` — `SELECT name FROM sqlite_master
WHERE type='table' AND name='solutions_v1'` — which is the whole of D7.

### 5.4 `src/nethackers/hub/api.py` (modified)

`GET /` grows a `Request` parameter and returns a `Response` whose body and
media type depend on `prefers_markdown(request.headers.get("accept"))`. Both
branches set `Vary: Accept` (I2). `HEAD /` is added to the same route.

Two new routes, `GET /index.md` and `GET /llms.txt`, return the brief as
`text/plain; charset=utf-8` unconditionally.

### 5.5 `src/nethackers/hub/web/index.html` (modified)

One line in `<head>`, next to the existing link-preview block:

```html
<link rel="alternate" type="text/markdown" href="/index.md">
```

## 6. The brief's content contract

Order is part of the design (D6). Budget ≤ 4 KB.

1. **Title and one sentence.** What NetHackers is, and the link to the human
   page.
2. **Read this first** — the three corrections, as plain declarative sentences:
   the board was reset for arena major 2 and an earlier epoch is archived, so
   zero registered programs is the expected state, not a fault; scores are
   measured on linux/amd64 and the hub refuses a native arm64 score; the source
   repo is private, and the CLI installs from PyPI.
3. **State of the board** — programs, hackers, ascensions, best progression,
   last registration, and both AutoAscend floors, each labelled with the tier it
   belongs to, plus the `generated_at` stamp.
4. **Get started** — `uv tool install nethackers`, `nethackers doctor`, and the
   quickstart, with requirements inline (Python 3.11+, Docker or Podman,
   Rosetta on Apple silicon).
5. **How scoring works** — the program is the unit of evaluation; 73 identities
   × 15 seeds; Public vs Private Dungeons in two sentences.
6. **For agents** — `/openapi.json` for the full contract, and one line each
   for `/stats`, `/board`, `/elites`, `/programs`, `/baseline` and
   `/recognition`, including the `tier` parameter that selects Public or
   Private Dungeons.
7. **Source** — repo (private; request access), PyPI, `docs/harness.md`.

Sections 4 and 5 are condensed from `README.md`, which is the source of truth
for both. §8 describes the drift tripwire.

## 7. Invariants

- **I1.** A request that does not explicitly list `text/markdown` never
  receives markdown from `/`. Browsers, `curl` and every `*/*` client keep
  today's behaviour exactly.
- **I2.** `/` sends `Vary: Accept` on **both** branches. A cache that stores
  one representation under a bare URL key will serve it to the other kind of
  client. Caddy currently runs no response cache, so today's exposure is the
  clients' own caches (Claude Code holds a fetch for 15 minutes) — but this is
  the invariant that makes a future CDN safe, and Cloudflare only began honouring
  origin `Vary` at all on 2026-07-02.
- **I3.** Every number in the brief is read from the store during that request.
  The brief contains no transcribed figures.
- **I4.** The brief never states a date the store cannot supply. A missing value
  renders as a dash, following `read_stats`' existing discipline for
  `last_registered_at`.
- **I5.** The brief exposes no hidden seed. It carries aggregate verified
  scores only — never a seed, never a per-episode row. `tests/hub/test_hidden_seed_exposure.py`
  is extended to cover it.
- **I6.** Every non-`.py` file under `src/nethackers/hub/web/` is covered by a
  `force-include` entry in `pyproject.toml`. Breaking this ships a wheel whose
  hub 500s on first request.
- **I7.** The rendered brief is ≤ 4 KB.

## 8. Known failure modes

**The brief drifts from `README.md`.** Two documents state the install commands;
only one is read by a human maintaining the project. Mitigation: a test asserts
that every shell command line in the brief appears verbatim in `README.md`. That
makes the README the source of truth and turns drift into a red test rather than
a wrong instruction served to an agent.

**The packaged asset is missing from the wheel.** `index.html` needs an explicit
`force-include` today, so `brief.md` will too, and nothing currently notices if
it is forgotten — the repo test suite reads from `src/` and passes. Mitigation:
I6, tested by parsing `pyproject.toml` and comparing against a directory listing.

**A cache in front serves markdown to a browser.** Mitigated by I2.

**The negotiation fires on a client that cannot read markdown.** Mitigated by
D2 (explicit listing required) and by the fact that a client sending
`text/markdown` has stated the capability.

**Verification is confounded by the agent's own cache.** Claude Code caches a
fetched URL for 15 minutes. Any re-run must cache-bust with a distinct query
string, or it measures the previous build.

## 9. Testing

**`tests/hub/test_negotiate.py`** — the truth table, using real captured Accept
strings rather than invented ones:

| Accept | Expect |
|---|---|
| `text/markdown, text/html, */*` (Claude Code, measured) | markdown |
| `text/markdown,text/html;q=0.9,application/xhtml+xml;q=0.8,…` (Cursor, measured) | markdown |
| `text/markdown, text/plain;q=0.9, */*;q=0.8` (Cursor, second capture) | markdown |
| `text/html,application/xhtml+xml,application/xml;q=0.9,…,*/*;q=0.8` (ChatGPT-User / Chrome, measured) | HTML |
| `*/*` (curl, measured) | HTML |
| absent | HTML |
| `text/markdown;q=0.5, text/html` | HTML — the case Stripe gets wrong |
| `text/markdown;q=0, text/html` | HTML |
| `text/*` | HTML — a wildcard is not a request |

**`tests/hub/test_brief.py`** — via `TestClient`: content type per route, `Vary:
Accept` on both branches of `/`, `HEAD /` is 200, `/index.md` and `/llms.txt`
are `text/plain` and byte-identical, the 4 KB budget, the README command-drift
tripwire, the `force-include` coverage check, the hidden-seed assertion, and two
data states — a populated fixture store (numbers appear) and an empty one with
archive tables present (the reset sentence appears, no invented date).

**Behavioural re-run.** The in-scope scenario briefs (bare link, "install this",
"what is this?", "who's winning?") are re-run against a
`cloudflared` preview of a local hub — once with fixtures, once with an empty
store — and the transcripts diffed against the 2026-09-16 baseline captured in
this design. The bar is not "the agent is happy": it is that no agent reports
data loss, that the install plan is sourced from the site rather than
reconstructed, and that the token cost of answering "what is this" falls by an
order of magnitude. Note that the preview URL is public for the life of the
tunnel; it serves fixture data only.

## 10. Rollout

1. Merge. The change is additive: no existing response changes for any client
   that does not ask for markdown (I1).
2. Release and **deploy** — the last two releases both carried `[skip
   hub-deploy]`, so production is still serving v0.27.0 and would not otherwise
   pick this up.
3. Re-run the behavioural suite against production once deployed.

## 11. Out of scope

- **Discoverability.** `robots.txt`, `sitemap.xml`, the meta description and
  search indexing are deliberately parked. The site is currently absent from
  search results even for its own hostname; that is a real problem and a
  different one.
- **The dead calls to action.** Three links on the page and one in the FAQ point
  at `github.com/dunnolab/nethackers`, which 404s for anyone outside the org.
  The brief will say the repo is private; the page still links to a 404.
- **Server-rendering the page's live data.** The deeper fix for a fetcher
  reading HTML. Larger, and not required by any scenario once the brief exists.
- **The `[TO BE EXPANDED]` placeholder** live in the production FAQ.
