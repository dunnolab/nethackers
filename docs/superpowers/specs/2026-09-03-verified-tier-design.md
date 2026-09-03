# Verified tier: trusted scoring on hidden seeds, submitted to the hub

Date: 2026-09-03
Branch: `vkurenkov/verify-hub-submit-layer`
Status: design (awaiting review)

## 1. Goal

Give the hub a second, trustworthy score for each program alongside the existing
self-reported one. A trusted **verifier** (authenticated by a trusted token) runs
on any server(s) the operator owns; it pulls registered programs, scores them on
**secret ("hidden") seeds the author never saw**, and submits the results to the
central hub as a **`verified`** tier.

Two orthogonal ideas, named apart on purpose:

- **verified** is the *trust level* — the score was produced by a trusted
  verifier (a trusted token), not self-asserted by the author.
- **hidden** is the *seeds* this verification currently uses — secret seeds,
  unguessable in advance.

Keeping them separate is deliberate: today every `verified` atom comes from a
hidden-seed run, but the *same* `verified` tier can later hold trusted
re-evaluations of **public-seed** atoms (verifying self-reported public scores).
That future path reuses this whole flow unchanged, distinguished only by which
secret it ran under (a public re-verification carries `secret_fingerprint =
sha256("public")`).

This is the deferred M2 "held-out evaluation worker" (`worker/server.py` is its
reserved stub). It exists because self-reported scores overfit: champions that
lead on the public practice seeds regress on fresh ones. The verified (hidden-seed)
score is the de-noised measurement.

Two tiers, complementary — **not** claim-versus-check:

- **self-reported** — the author's score on the *public* practice seeds
  (`secret="public"`, reproducible by anyone). Unchanged by this work.
- **verified** — an independent, trusted score on *hidden* seeds (hub-held secret;
  nobody can reproduce them in advance).

Design stance: **minimal and maximally compatible with the current
architecture.** Reuse the existing eval path, the existing atom/Evidence model,
and the existing `baseline_atoms` storage pattern. No new queue, no lease, no
sandbox rearchitecture, no migration of the `atoms` table.

## 2. Audience & surface

The verifier is an **operator** tool. It needs the hub's secret and a verifier
token, so it must not appear in the participant CLI. The repo already splits
entrypoints by audience in `pyproject.toml`:

- `nethackers` → participants (`submit`, `eval`, `evolve`, `board`, …) — **untouched**
- `nethackers-hub` → the hub server
- `nethackers-worker` → the verifier (currently the stub we fill in)

Participants keep `nethackers eval` for public-seed scoring and never see the
verification path.

## 3. The mechanism (secret + hidden seeds)

A NetHack game is determined by its seeds. We derive them (existing code,
`arena/seeds.py`):

```
game seeds for episode i  =  HMAC(secret, evaluation_id, i)
```

`evaluation_id` is an inert constant (`"local"` today) that we **do not vary or
surface** — with a different secret, the same constant already yields unrelated
games (HMAC with a different key is independent). So the only thing that
distinguishes hidden games from public games is the **secret**:

- public games: `secret = "public"` (known, reproducible)
- hidden games: `secret = <random, hub-only>` (unguessable → unseen)

The config a verifier needs is therefore exactly **`{secret, seeds}`**, where
`seeds` is an explicit **list of seed ids** (a "seed" is the trajectory id the
arena feeds to `trajectory_spec`, which the atom stores). These are **not**
`0..14` — those are the public practice indices, guessable and overlapping with
the public set. They are **randomly generated in a large space** (up to ~10^7,
"millions"), 15 of them, run against the fixed **73 identities**
(`hub/objectives.py: IDENTITIES`). `trajectory_spec` already accepts any
non-negative id, so large random ids need **no arena change**.

Both `secret` and `seeds` are **secret material**: with either one unknown the
games are unguessable, so they are two independent locks (defense in depth). They
are generated once and stored as GitHub secrets (§4a). Rotation changes the
config — the secret and/or the seed list; see §7.

### Secret fingerprint

`secret_fingerprint(secret) = sha256(secret)` (already in `arena/seeds.py`,
unchanged) is a safe public label for "which secret produced this score." It
covers the **secret only** — the seed ids are already stored per-row in the
`seed` column, so rotating the seeds yields different `seed` values (hence new
rows) on its own. The fingerprint's only job is the one case the `seed` value
can't catch: the *same* seed under a *different* secret is a different game. It
never exposes the secret.

## 4. Components (each maps to existing code)

### 4a. Hub config, held by the hub, fetched by token

- **Canonical storage = GitHub Actions secrets**, injected into the hub env on
  deploy (the mechanism the hub already uses for its production secrets) so the
  values survive a hub/DB rebuild and rotate in one place:
  - `NETHACKERS_HIDDEN_SECRET` — the random HMAC secret (≠ `"public"`);
  - `NETHACKERS_HIDDEN_SEEDS` — the random large seed ids (millions space);
  - `NETHACKERS_VERIFIER_TOKENS` — a **set** of verifier tokens, one per trusted
    person/box (so submissions are attributable — see below).
  All logged only by fingerprint, never in the clear.
- New `GET /verify/config` (verifier-token auth) →
  `{"secret": "…", "seeds": [4839201, 1029384, …]}`. The hub is the single
  runtime source; verifiers retrieve it by token, so a rotation (update the
  GitHub secret → redeploy) reaches every verifier.

Auth & attribution: a small check alongside the existing `AuthProvider` — a
bearer token in the `NETHACKERS_VERIFIER_TOKENS` set resolves to the "verifier"
principal (separate from `GitHubAppAuth`; the only principal that may read the
secret or write the verified tier). The hub records `sha256(token)` on every atom
that token writes (§4d), so each verified score is traceable to the verifier that
produced it — the handle for catching an insider who submits fraudulent scores.

### 4b. Eval on hidden seeds = `eval_batch` + a secret (one new argument)

`eval/runner.py: eval_batch(...)` already runs the pinned arena image over an
`ObjectiveSpec` batch and stamps the resolved image **digest** into
`Evidence.evaluator_image`. Change:

- add one parameter `secret: str = "public"` (default preserves every current
  caller's behavior exactly);
- pass it to the container via **environment** (`-e NETHACK_ARENA_SECRET=…`),
  **never on argv** (so it is not in `/proc/<pid>/cmdline`);
- `arena/run.py` reads the secret from `NETHACK_ARENA_SECRET` when set, else
  falls back to the existing `--secret` default (`"public"`). `evaluation_id`
  stays `"local"` — no new flag.

No other change to the arena/eval internals. The batch is the existing
per-identity batch shape over the config's seed list:
`[(i, identity) for identity in IDENTITIES for i in config.seeds]`.

### 4c. Submission: `POST /verify`

A small sibling to `POST /register`, **reusing the same storage machinery**
(`evidence_to_atoms`-style conversion → insert), with these differences:

- **auth**: verifier token (not a GitHub user token);
- **no ownership check**: the verifier scores programs it does not own, so the
  `owns_repo` gate from `register()` is skipped;
- **solution must already exist**: the program was registered via self-report;
  `owner` is copied from its `solutions` row so attribution stays with the author;
- **tier**: `"verified"`;
- **parity enforced** (unlike self-reported): `evidence.evaluator_image` must
  equal the pinned `_image_pins.ARENA_IMAGE` digest, else reject;
- **secret fingerprint checked**: the submitted `secret_fingerprint` must equal
  the hub's `sha256(secret)`, rejecting stale-secret submissions;
- **attribution stamped**: `sha256(presented verifier token)` is written to every
  atom (`verifier_token_fingerprint`);
- **incremental / idempotent**: the body may carry any subset of the
  73 × |seeds| grid (the verifier submits per identity, see §4e); each
  `(identity, seed)` must lie within the spec (identity ∈ the 73, `seed` ∈ the
  config's seed list); finite metrics required. Re-submission is a no-op via
  `INSERT OR IGNORE`.

Request shape mirrors `RegisterRequest` minus the manifest:
`{ "reference": {"repo", "commit"}, "evidence": {…, "tier": "verified",
"evaluator_image": "sha256:…"}, "secret_fingerprint": "…" }`.
Response: `{ "inserted": N, "ignored": M, "coverage": {"done": d, "total": 73×|seeds|} }`.

**Program-level outcome — `POST /verify/attempts`** (verifier-token auth): a
**separate resource** from the atoms submission, where the verifier reports the
terminal outcome of each attempt — `succeeded`, or `failed` with a `failure_kind`
and short `message` — into `verified_attempts` (§4d), attributed by token
fingerprint. This is what turns a failed run into a visible record instead of a
silent worker skip.

The register ladder logic is factored so `register()` and this path share the
common checks (reference exists, batch membership, finite metrics, parity) and
differ only in ownership + tier + target table.

### 4d. Storage: a separate `verified_atoms` table

Mirrors the existing `baseline_atoms` table (a different kind of eval kept in its
own table, read by the boards as an overlay). **The `atoms` table and the whole
self-reported path are untouched — no migration of the load-bearing table.**

```sql
CREATE TABLE IF NOT EXISTS verified_atoms (
    solution_digest             TEXT NOT NULL,
    owner                       TEXT NOT NULL,
    identity                    TEXT NOT NULL,
    seed                        INTEGER NOT NULL,
    progression                 REAL NOT NULL,
    milestone                   TEXT,
    ascended                    INTEGER NOT NULL,
    status                      TEXT NOT NULL,
    turns                       INTEGER NOT NULL,
    steps                       INTEGER NOT NULL,
    evaluator_image             TEXT NOT NULL,
    secret_fingerprint          TEXT NOT NULL,
    verifier_token_fingerprint  TEXT NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, identity, seed, secret_fingerprint, evaluator_image)
);
```

The unique key carries **`secret_fingerprint`** and **`evaluator_image`** so that
a secret rotation or an arena-image re-pin produces *new* rows rather than
`INSERT OR IGNORE`-swallowing a genuinely different measurement behind a stale
one. A **seed** rotation needs no fingerprint help: the new random seed ids are
themselves different `seed` values, so they land as new rows (and the board
filters to the current seed list — §4f). `verifier_token_fingerprint` is **not**
in the key — it records *who* wrote the surviving row (first write wins), the
handle for tracing fraud. Within one secret + one image (the normal case), the
effective key is `(solution, identity, seed)` — clean incremental idempotency for
the verifier. (A future public-seed verification lands in this same table with
`secret_fingerprint = sha256("public")` and public `seed` ids.)

Coverage of a program = count of `verified_atoms` whose `seed` ∈ the current seed
list, at the current `secret_fingerprint` and image; full = 73 × |seeds| (1095
with the default 15-seed list).

A second small table, **`verified_attempts`**, makes failed attempts visible —
they otherwise produce no atoms and look identical to "never tried":

```sql
CREATE TABLE IF NOT EXISTS verified_attempts (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_digest             TEXT NOT NULL,
    secret_fingerprint          TEXT NOT NULL,
    evaluator_image             TEXT NOT NULL,
    verifier_token_fingerprint  TEXT NOT NULL,
    status                      TEXT NOT NULL,     -- 'succeeded' | 'failed'
    failure_kind                TEXT,              -- build_failed|crashed|hung|infra_error
    message                     TEXT,              -- short log tail on failure
    identities_done             INTEGER NOT NULL,  -- coverage reached this attempt
    at                          TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Append-only, one row per terminal program attempt — the "did we try this, and
what happened" log, attributed by `verifier_token_fingerprint`.

### 4e. The verifier (`nethackers-worker`)

Fill in the reserved entrypoint / `nethackers/worker/` subpackage.

**Daemon (the happy path) — a loop that does not die:**

1. `GET /verify/config` (refresh each iteration).
2. Ensure the pinned arena image is present (pull by digest).
3. `GET /verify/candidates` → programs lacking full verified coverage at the
   current fingerprint+image, ranked by self-reported board relevance; pick one
   (random among the top few to spread load across boxes).
4. Clone `repo@commit` (reuse the existing `hubclient/pull.py` path).
5. For each identity not yet covered, run the seed-list batch via `eval_batch(...,
   secret=<fetched>)` in the arena container, then `POST /verify` for that
   identity. Submitting per identity gives free resume: a worker that dies at
   40/73 has durably stored 40; the next reads coverage and finishes the rest.
6. Report the attempt outcome via `POST /verify/attempts`, then repeat.

**Resilience:** a program that fails to build / crashes / hangs is caught and
**reported to the hub** via `POST /verify/attempts` (so the failed attempt is
visible), then skipped — it never kills the worker; a deterministic program
failure is not auto-retried at the same config+image. Hub-unreachable / 5xx →
exponential backoff with jitter; results are held and retried (a small local
spool) so a hub blip never discards compute. Graceful SIGTERM finishes the
current identity and exits.

**One-shot** (secondary): `nethackers-worker <program>` / `--once` — the same
path with a fixed target, exit code = outcome. For targeted checks, not the
happy path.

**Selection endpoint** `GET /verify/candidates` (verifier-token auth) keeps "what
is worth scoring" on the hub (reusing the existing board/coverage queries) and is
deliberately a **separate resource** from `POST /verify` — get-work and
submit-result never share an endpoint. It also excludes programs whose latest
attempt at the current config+image was a deterministic failure (from
`verified_attempts`), so workers don't re-run a broken program forever; an image
re-pin or a manual one-shot re-opens them.

### 4f. Views (additive, mostly free)

Boards / elites / program pages read `verified_atoms` as an **overlay**, exactly
as they already read `baseline_atoms` for the AutoAscend floor: a per-cell
verified progression next to the self-reported one, plus a coverage indicator.
Ranking stays on self-reported for now; flipping the leaderboard to rank by
verified is a later, separate step once coverage is broad. The public read for the
overlay is `verified_atoms` filtered to the current seed list + secret fingerprint
+ image, exposed as **per-identity aggregates only** — never raw per-seed rows or
seed ids (the seeds are secret; unlike self-reported's `GET /atoms`). Raw seeds
and the per-atom token fingerprints stay behind the verifier token.

The program page also shows a **verification status** from `verified_attempts` +
coverage — `verified (n/total)`, `attempted · failed: <kind>`, or `not attempted`
— so a failed attempt is visible at a glance; the raw `message` / attribution
stays behind the verifier token.

## 5. Data flow (end to end)

```
operator box (holds verifier token only)
  │  GET /verify/config ──────────► hub returns {secret, seeds:[random…]}
  │  GET /verify/candidates ──────► hub returns programs missing verified coverage
  │  clone repo@commit (public)
  │  docker run --rm --network none  arena@<pinned digest>
  │     -e NETHACK_ARENA_SECRET=<secret>   (secret via env, judge derives seeds)
  │     -v sol:/sol:ro  -v out:/out
  │  per identity → Evidence(tier="verified", evaluator_image=<pinned digest>)
  │  POST /verify {reference, evidence, secret_fingerprint} ──► hub:
  │        check token · solution exists · batch∈spec · parity==pinned ·
  │        secret_fingerprint==current → INSERT OR IGNORE into verified_atoms
  │        (each row stamped with sha256(verifier token))
  │  POST /verify/attempts {outcome} ──► verified_attempts
  ▼
hub boards read verified_atoms (current seed list + secret fingerprint + image)
as an aggregate overlay
```

## 6. Error handling

- **Parity mismatch** (`evaluator_image` ≠ pinned): `POST /verify` rejects (400).
  The verifier only ever runs the pinned image, so this catches misconfiguration.
- **Stale secret** (secret fingerprint ≠ current): rejected (409). The verifier
  refreshes config each loop, so this only bites a worker mid-rotation; it
  re-reads and continues.
- **Unknown / unregistered program**: 404; the verifier skips it.
- **Program-level failure** (can't clone/build, the container dies, or the eval
  hangs — no complete score produced): reported to the hub via
  `POST /verify/attempts` as `failed` with a `failure_kind`
  (`build_failed`/`crashed`/`hung`/`infra_error`), a short message, and the token
  fingerprint, so the attempt is **visible** (not a silent skip). Deterministic
  program failures (`build_failed`/`crashed`/`hung`) are not auto-retried at the
  same config+image and drop from candidates until the image re-pins or an
  operator re-triggers; `infra_error` (the worker's fault) stays retry-eligible.
  Per-*episode* bot crashes/timeouts are different: the arena already turns them
  into zero-progress atoms (`status=bot_error`/`bot_timeout`), so a bot that loads
  but misbehaves still gets a real verified score (~0) on the board.
- **Hub write contention**: the hub's shared-connection write race (hub security
  audit, 2026-08-29) also touches `verified_atoms` writes. Mitigation for this
  minimal version: the verifier asserts `inserted + ignored == len(batch)` and
  retries the idempotent batch on mismatch or `503`. The proper fix (per-request
  DB connection / WAL) is tracked separately and is **not** a prerequisite here
  given single-operator, low write volume.

## 7. Secret / seed rotation

To retire a hidden-seed epoch (suspected leak, or a fresh epoch): change the
`NETHACKERS_HIDDEN_SECRET` and/or `NETHACKERS_HIDDEN_SEEDS` GitHub secrets and
redeploy. Consequences, by design:

- Rotating the **secret** → a new `secret_fingerprint` → new rows that cannot
  collide with the old.
- Rotating the **seeds** → new random `seed` values → new rows on their own (no
  fingerprint needed).
- Boards read only the current seed list + secret fingerprint, so they show the
  new set as coverage rebuilds.
- Old rows are **kept as history — never pruned or wiped**; the `secret_fingerprint`
  and the (distinct) seed values keep every past epoch cleanly separated from the
  current one.

## 8. Security posture & residual risk (stated honestly)

The verifier runs **untrusted, potentially adversarial** submitted code while the
hub's secret is present in the eval container. This minimal version:

- keeps the current container isolation (`docker run --rm --network none`,
  solution mounted read-only);
- moves the secret **off argv into an env var**, so it is not exposed in
  `/proc/<pid>/cmdline`.

**Residual risk (not closed here, and deliberately so, to stay minimal):** a
malicious bot sharing the judge's uid/PID-namespace could still read the secret
via `/proc` or via two existing code holes in `arena/`:
(a) the judge unpickles the bot's pipe replies (`sandbox.py` `Connection.recv()`),
and (b) the submission sits on the judge's `sys.path` (`run.py`), letting a bot
shadow a lazily-imported module. If a bot reads the secret it can, in principle,
exfiltrate it by encoding it into its own per-episode results (which are
published). This matters only against an adversarial submission; the current
participant set is the operator's own lab.

**Recommended follow-up hardening (separate work, cheap → structural):** close
(a) with a fixed struct frame instead of `recv()`; close (b) by dropping the
`sys.path.insert`; add the mutator cage's resource caps (`--pids-limit
--memory --cpus --cap-drop ALL --read-only --tmpfs no-new-privileges`); and, for
full robustness, run the bot in its own container talking to the judge over one
socket (observations in, one integer out) so the secret never shares a boundary
with untrusted code. Tracked as a follow-up; **not** in this scope.

## 9. Testing

- **`eval_batch` secret plumbing**: unit test with the existing injectable
  `runner`/`image_digest_resolver` fakes — assert `-e NETHACK_ARENA_SECRET` is
  passed and the secret never appears on argv; default path still uses `"public"`.
- **`POST /verify`**: hub tests with a stub verifier token — verified_atoms
  written, `owner` copied from the solution, parity enforced, wrong fingerprint
  rejected, unregistered program 404, idempotent re-submit, per-identity partial
  submit accepted, `verifier_token_fingerprint` stamped.
- **`POST /verify/attempts`**: a failed attempt is recorded and surfaced; a
  deterministically-failed program is excluded from candidates.
- **Schema**: `verified_atoms` / `verified_attempts` creation is idempotent; a
  secret rotation inserts a second fingerprint's rows without collision.
- **Verifier loop**: fake hub client + fake eval — resume from partial coverage; a
  crashing program is reported and skipped, not fatal; backoff on hub errors;
  SIGTERM drains.
- **E2E (local stack)**: `make hub`, register a program (self-report), run a
  verification against it, assert the verified overlay shows on the board
  (per `docs/local-stack.md`).

## 10. Decisions & scope

**Decided:**
- Tier named **`verified`** — the *trust level* (a trusted token produced it);
  **`hidden`** names the *seeds* it currently uses. The same `verified` tier can
  later hold trusted re-evaluations of public-seed atoms (same flow,
  `secret_fingerprint = sha256("public")`).
- It is an independent measurement, not a re-check of the author's number.
- Config is `{secret, seeds}`; `evaluation_id` is an inert constant, not surfaced.
- `seeds` are random large ids (millions), secret; both secret and seeds live in
  GitHub secrets → hub env → verifiers by token. Public verified views are
  per-identity aggregates (raw seeds never exposed).
- Separate `verified_atoms` table (baseline_atoms pattern); `atoms` untouched.
- Every verified atom is stamped with `sha256(verifier token)` for attribution
  (tracing insider fraud); verifier tokens are a set, one per trusted box.
- Operator surface is `nethackers-worker`, not the participant CLI.
- Get-work (`GET /verify/candidates`) is a separate resource from submit
  (`POST /verify`).
- Parity enforced for verified; `secret_fingerprint` (`sha256(secret)`) labels the
  secret epoch; seeds distinguish themselves by their own `seed` value.
- History is retained across rotations — **never pruned or wiped**; the fingerprint
  + seed values keep epochs separated.
- Failed attempts are recorded (`verified_attempts` + `POST /verify/attempts`) and
  surfaced (candidates skip deterministic failures; the program page shows a
  verification status), so an attempt that produced no score is visible, not a
  silent skip.
- Env vars settled: `NETHACKERS_HIDDEN_SECRET`, `NETHACKERS_HIDDEN_SEEDS`,
  `NETHACKERS_VERIFIER_TOKENS` (in-container the secret is still passed as the
  existing `NETHACK_ARENA_SECRET`).
- The verified board **does** need an AutoAscend **hidden-seed** baseline (re-run
  AA on the secret seeds) for an honest "Δ vs AA" — acknowledged as needed, but
  **deferred** to a follow-up. The initial verified overlay shows raw verified
  scores; the hidden-seed AA floor lands later.

**Open:** none — all decisions settled.

**Explicitly out of scope (deferred):** the AutoAscend hidden-seed baseline
(needed, deferred — above); hard lease/queue, message broker, per-job tokens,
exactly-once, ranking-flip to verified, deeper sandbox isolation (§8), the hub
per-request-connection refactor, signed submissions, multiple concurrent secret
epochs, public-seed verification (future, but the tier and tables already
accommodate it).
