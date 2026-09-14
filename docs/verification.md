# The verification layer

```
        ___                       ___
       / _ \_   _| |__ | (_) ___  |   \ _  _ _ _  __ _ ___ ___ _ _  ___
      / __/| |_| | '_ \| | |/ __| | |) | || | ' \/ _` / -_) _ \ ' \(_-<
      \___| \__,_|_.__/|_|_|\___| |___/ \_,_|_||_\__, \___\___/_||_/__/
                                                 |___/
                          vs.  Private Dungeons
```

The hub carries two scores per program. This document explains what each one
means, how the private tier is computed, and how the verifier that computes it
schedules its work.

- [Two tiers](#two-tiers)
- [Where hidden seeds come from](#where-hidden-seeds-come-from)
- [The verifier](#the-verifier)
- [The scheduler](#the-scheduler)
- [The AutoAscend floor](#the-autoascend-floor)
- [Epochs](#epochs)
- [What the public can read](#what-the-public-can-read)

---

## Two tiers

| | **Public Dungeons** | **Private Dungeons** |
|---|---|---|
| `tier` in DB and API | `self-reported` | `verified` |
| Seeds | 15 published per identity | secret; count is deployment config (15 in production) |
| Who produced the number | the author | a trusted verifier on our hardware |
| Can you reproduce it | yes, given a deterministic bot | no |
| Storage | `atoms` | `verified_atoms` (side table) |

### Why two

The two tiers answer different questions, and both are worth asking.

The public tier asks **how good is this bot on the seeds everyone can see** — the
target you develop against, and the one the evolutionary harness optimizes. That
is by design: NetHack is unsolved by a wide margin, and the starting bet is that
plainly getting better at the public dungeons is where the first generalizable
improvements come from.

The private tier asks **does it transfer** — the same program on games it has
never met, run by someone other than its author. Neither answer substitutes for
the other; a program that is strong publicly and weak privately is telling you
something specific, not confessing to a crime.

The website therefore defaults to Private, and names the tiers by *which dungeons
the number came from* rather than by who is trusted.

### Verified ≠ hidden

Two orthogonal ideas, deliberately named apart:

- **verified** is the **trust level** — a trusted worker with a trusted token
  produced this number, rather than the author asserting it.
- **hidden** is the **seeds** it happened to use — secret, unguessable in advance.

Today every `verified` atom comes from a hidden-seed run, so the two coincide.
But the same tier can later hold trusted re-evaluations of *public*-seed results
(checking a self-reported claim), distinguished only by which secret they ran
under — a public re-verification carries `secret_fingerprint = sha256("public")`.
That path is not wired up today — the hub holds exactly one secret and one seed
list, and rejects any fingerprint that doesn't match it — but keeping the two
words apart is what leaves room for it.

The website rename is **presentation only**. `tier` values in the database and
the API stay `self-reported` and `verified`; the page maps them to "Public
Dungeons" / "Private Dungeons" for display, mostly through a single `TIERS`
lookup.

---

## Where hidden seeds come from

A NetHack game is determined by its seeds, and we derive them
([`arena/seeds.py`](../src/nethackers/arena/seeds.py)):

```
digest    = HMAC-SHA256(secret, b"nethack-arena\0{evaluation_id}\0{trajectory_id}")
core, display, level, bot seeds = four 63-bit slices of that digest
```

`evaluation_id` is an inert constant (`"local"`). It is not varied and not
surfaced — with a different `secret`, the same constant already yields completely
unrelated games, because HMAC under a different key is independent. So **the
secret is the only thing that separates public games from private ones.**

- The **public secret** is published. That is what makes `nethackers eval`
  reproducible on your laptop and identical to everyone else's run.
- The **hidden secret** is held by the hub and handed only to a verifier
  presenting a trusted token.

Note what this does and does not protect. The derivation itself is *not* a
secret: `nethackers.arena.seeds` ships inside the mutator image, and the public
secret is the literal string `"public"`, so an agent in the cage can reproduce
every public game exactly — which is fine, they are published. What it cannot
reach is the hidden secret, which lives only in the hub's and verifier's
environment (`NETHACKERS_HIDDEN_SECRET`) and is never baked into any image.
That absence, not any missing module, is what makes hidden seeds unguessable.

A `secret_fingerprint` — `sha256(secret)` — travels with every submission so the
hub can reject work computed under a stale secret without ever revealing the
secret itself.

---

## The verifier

The verifier is an **operator tool**, not a participant one. It needs the hub's
hidden secret and a trusted token, so it ships as its own entry point and never
appears in the participant CLI:

| Entry point | Audience |
|---|---|
| `nethackers` | participants — `eval`, `evolve`, `submit`, `board`, … |
| `nethackers-hub` | the hub server |
| `nethackers-worker` | the verifier |

It runs on hardware the operator controls. Given a token, it:

1. Fetches the hidden config (secret + seed list) from the hub.
2. Pulls a registered program by `repo@commit`.
3. Scores it, **one identity at a time**, through the ordinary arena eval path —
   the same container, the same image, the same code participants run. What
   differs is the secret it is handed and the trajectory ids it runs; the secret
   alone would be enough to make the games unrelated.
4. Submits each identity's atoms to the hub as soon as that identity finishes.

Submitting per identity rather than per program is deliberate: a full pass is
73 identities × 15 seeds ≈ 1095 episodes and takes hours. A crash at identity 50
keeps the 49 already banked.

**Three modes, plus a one-shot baseline:**

```bash
# one-shot: verify a single program, then exit
nethackers-worker github.com/owner/nethacker@<sha> --token "$TOKEN"

# one candidate pass, then exit  (cron-friendly)
nethackers-worker --once --token "$TOKEN"

# daemon: fetch, verify, repeat, forever  (the default)
nethackers-worker --token "$TOKEN"

# one-shot: compute the AutoAscend hidden-seed floor
nethackers-worker --baseline --tree roots/autoascend --token "$TOKEN"
```

`--max-parallel-evals` caps concurrent episodes per eval. The default (8) is a
middle value: it oversubscribes a 4-CPU node and underuses a 16-core one, so tune
it per box. More is not automatically faster — oversubscribing contends for CPU,
and the arena's per-action timeout is wall-clock, so contention can depress the
score itself.

---

## The scheduler

There is **no queue and no lease**. The hub computes the work list on demand from
what is already stored, which means a verifier that dies mid-program simply loses
that program's remaining identities — nothing to reconcile, nothing to expire.

### Hub side: picking candidates

`GET /verify/candidates` (`hub/verify.py:verify_candidates`) walks registered
solutions and returns those still needing coverage under the current epoch:

- **Skip** anything already fully covered (`done >= 73 × len(seeds)`).
- **Skip** anything whose last attempt failed *deterministically* — a program
  that could not be cloned or that crashes on load will fail identically next
  time. Transient failures (a hub blip during submission) stay retry-eligible.
  The distinction is carried explicitly as `failure_kind`: `build_failed`,
  `crashed`, and `hung` are deterministic; `infra_error` is not.
- **Sort by coverage ascending**, ties broken by program id, and return the
  first `--limit` (default 8).

Least-covered-first means a newly registered program gets attention before an
already-half-verified one, so coverage spreads rather than deepening on whatever
happened to be registered first.

### Worker side: the loop

```
   ┌─▶ GET /verify/config      (secret + seed list)
   │   GET /verify/candidates  (limit N, least-covered first)
   │        │
   │        ├── empty? ── sleep 30s ──┐
   │        │                          │
   │        ▼                          │
   │   for each candidate:             │
   │     pull → eval 73 identities →   │
   │     submit each identity's atoms  │
   │        │                          │
   └────────┴──────────────────────────┘
```

Resilient on two axes, both of which matter for a process meant to run for weeks:

- **Hub outages back off exponentially** — base 10s, doubling, capped at 600s,
  reset on any successful fetch. The only unguarded hub calls live inside that
  guard, so a blip can never crash the daemon or turn it into a hot loop.
- **A failing candidate is skipped, never fatal.** `verify_program` reports its
  own failures and returns a status string rather than raising, and each candidate
  is defensively wrapped on top of that, so one bad program can't take the daemon
  down.

  One edge worth knowing if you operate this: the backoff only guards the two
  GETs. If those succeed but the *attempt-reporting POST* fails deterministically
  (schema skew, say), the attempt is never recorded, the same candidates come back
  next pass, and there is no sleep on the non-empty path — so the loop spins at
  clone speed rather than backing off.

One non-obvious trap worth repeating for anyone operating this: the hub client
must be constructed with an explicit timeout. `httpx` defaults to 5 seconds,
which silently discards a POST carrying a completed identity's 15 atoms — hours
of work thrown away because the hub was briefly busy. The worker pins 60s.

---

## The AutoAscend floor

A verified score is only meaningful next to a reference. `--baseline` computes
AutoAscend's own score on the same hidden seeds and stores it in an isolated
`verified_baseline_atoms` table.

Three deliberate differences from a normal verification:

- **It takes a local tree**, not a `repo@commit`. The floor is not a participant;
  it has no `solutions` row and must never get one.
- **It resumes.** At ~1095 episodes this run takes hours, so identities the hub
  already holds a complete batch for are skipped. Partial coverage is not
  coverage — an identity missing even one seed is recomputed in full, since a
  batch is submitted whole.
- **It has no attempt bookkeeping**, because attempts are keyed by solution
  digest and the floor has none. A failure surfaces as a non-zero exit and a log
  line.

The floor lives in its own table, read by its own function
(`read_verified_baseline`). Nothing filters the floor out of the participant
aggregate, because it was never in the same table to begin with.

---

## Epochs

Every verified atom is scoped by `(secret_fingerprint, arena_major, seed)`.
Reads filter on all three, which means a **delta can never be computed across two
different measurements**.

`arena_major` is an integer naming a set of arena image digests **declared to
score alike** — `ARENA_MAJOR` and `ARENA_MAJOR_BY_DIGEST` in
`src/nethackers/arena_version.py`. It is deliberately not the image digest. Two
images can differ byte-for-byte and score identically, which is exactly what a
change to process lifetime, logging, or an error message produces; keying the
tier on the digest meant every such rebuild silently discarded the entire
verified corpus. Each atom still *records* the exact digest that produced it, as
provenance — it just is not what groups atoms into a comparable set.

Three things end an epoch:

| Event | Effect |
|---|---|
| The hidden secret is rotated | All prior verified atoms fall out of every read |
| `ARENA_MAJOR` is bumped | Same — the previous major's atoms stop being read |
| A seed is retired from the list | That seed's atoms drop out of the fold |

**Re-pinning the arena image is not on that list.** A rebuild that cannot change
a score keeps the whole corpus, which is the point of the major.

### After a re-pin: classify the digest

A re-pin does come with an obligation. `ARENA_MAJOR_BY_DIGEST` is hand-edited and
lives outside the generated `_image_pins.py`, so a fresh pin is **unclassified**
until someone adds a line for it. An unclassified digest is rejected at
admission: every submission from a node running it gets a 400, and verification
stops accumulating.

So, on the PR that carries the re-pin, add the new digest to
`ARENA_MAJOR_BY_DIGEST` in `src/nethackers/arena_version.py` with a comment
saying what the rebuild changed. The one judgment to make is whether it **moves
scores**:

- **It does not** — a process-lifetime, logging, packaging or dependency change
  that cannot touch how an episode is seeded, stepped or scored. Classify it at
  the current `ARENA_MAJOR`. The corpus carries over; nothing else to do.
- **It does** — anything that can move an episode's outcome. Classify it at
  `ARENA_MAJOR + 1` and bump `ARENA_MAJOR` to match. See below for what that
  costs.

Two things fail loudly if you forget: the unit test in
`tests/test_arena_version.py`, and a step in `.github/workflows/sandbox-images.yml`
on the run that created the pin. Neither can check that the judgment is *right* —
only that it was made.

### Bumping the major, and what it actually costs

A bump touches **no rows**. Nothing is deleted, nothing is rewritten; the
previous major's atoms stay in the database and simply stop being read, and they
become readable again if the major is ever restored. So the cost is not data
loss — it is that **every program reports zero verified coverage the moment the
bumped hub starts**, and the corpus has to be re-earned by re-running the
evaluator over the hidden grid.

At the corpus size this hub was carrying when the major was introduced —
about 21,400 participant episodes plus a 1,095-episode AutoAscend floor — that is
roughly **five days of evaluator-node time**, during which the Private Dungeons
board is empty or partial and every Delta-vs-AutoAscend reads `—` until the floor
is recomputed too. Budget it as a scheduled re-measurement, not a config change.

If you maintain a fork, the practical rule is: routine rebuilds are free, and you
only pay when you change what the arena actually does.

---

## What the public can read

Public verified reads are **per-identity aggregates only** — mean progression,
deepest milestone reached, episode count. Never raw per-seed rows, never seed ids.
The seeds are the whole mechanism; publishing them, even implicitly through
per-seed results, would convert the private tier into a public one.

Per-program coverage counts do exist, but they leave the hub only through
token-gated routes (`POST /verify`'s response and `GET /verify/candidates`) — the
`verification_status` helper itself is not wired to any endpoint today. Those
counts are atoms covered out of `73 × len(seeds)`, and reveal nothing about which
games were played.

---

## See also

- [`../README.md#public-and-private-dungeons`](../README.md#public-and-private-dungeons) — the short version
- [`harness.md`](harness.md) — the contract, the loop, and the information diet
- [`../deploy/README.md`](../deploy/README.md) — operating the hub
- [`superpowers/specs/2026-09-03-verified-tier-design.md`](superpowers/specs/2026-09-03-verified-tier-design.md) — the original design
- [`superpowers/specs/2026-09-07-public-private-dungeons-design.md`](superpowers/specs/2026-09-07-public-private-dungeons-design.md) — the website rename
