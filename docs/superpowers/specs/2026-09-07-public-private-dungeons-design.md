# Public / Private Dungeons: populating the private tier on the website

Date: 2026-09-07
Branch: `vkurenkov/Web-Populate-Verified`
Status: design (awaiting review)

## 1. Goal

The hub has stored trusted hidden-seed results since v0.20, and an AutoAscend
hidden-seed floor since v0.22.0. The website shows none of it: the verified tier
is not merely empty, it is **hardcoded** empty in three places, and its grid note
still reads "M2b isn't live".

This work:

1. Populates every website table from the verified side-tables that already hold
   the data.
2. Renames the two tiers **on the website only** to say which dungeons a number
   came from, not who is trusted.
3. Gives Frontier Keepers and Greatest Breakthroughs their own tier switches.
4. Makes the private (hidden-seed) tier the **default** everywhere.

## 2. What this completes

This is the follow-up that `2026-09-03-verified-tier-design.md` deferred. That
spec shipped storage and ingest, and parked two items:

- *"Ranking stays on self-reported for now; flipping the leaderboard to rank by
  verified is a later, separate step once coverage is broad."* — **this spec.**
- *"The verified board does need an AutoAscend hidden-seed baseline … deferred to
  a follow-up."* — **shipped** in v0.22.0 (`verified_baseline_atoms`).

Nothing here contradicts that spec. In particular its rule stands: public
verified reads are **per-identity aggregates only, never raw per-seed rows or
seed ids**, because the seeds are secret.

State at time of writing (verification is running, so these grow):

| | rows | identities | programs |
|---|---|---|---|
| `verified_atoms` | 255+ | 18 / 73 | 1 |
| `verified_baseline_atoms` | 180+ | 13 / 73 | — |

Every identity present is complete at 15/15 seeds — the worker submits one
identity at a time, so there is no partial-identity noise to design around.

## 3. Vocabulary: Public / Private Dungeons

### 3.1 Why the tier names were wrong on the website

Two orthogonal axes were being carried by one word:

|  | published seeds | hidden seeds |
|---|---|---|
| **hacker ran it** | `self-reported` | impossible — the seeds are secret |
| **we ran it** | future: public-seed verification | what ships today |

"Verified" correctly names the *act* — a trusted worker re-ran the program and
vouches for the number — and it is true of **both** cells in the bottom row. The
verified-tier spec reserved it for exactly that, noting public-seed verification
would reuse the same tier with `secret_fingerprint = sha256("public")`.

So the word the website was missing is not a trust word at all. What makes a
private number worth looking at is that **the program never saw those seeds** —
a generalization property, and the one the held-out overfitting finding was
about.

### 3.2 The rename is website-only

`tier` values in the DB and API stay `self-reported` and `verified`. They are
accurate for the machinery and have a live consumer in `worker/verify.py`.
No migration, no stored-value change, no route rename.

The website maps them through **one** table, and every toggle label, table
caption, and sub-label reads from it — no literal tier string appears in markup:

```js
const TIERS={
  "self-reported":{label:"Public Dungeons (15)",  caption:"PUBLIC DUNGEONS",
    note:"15 seeds per identity, run and reported by the hacker — unaudited"},
  "verified":     {label:"Private Dungeons (15)", caption:"PRIVATE DUNGEONS",
    note:"15 secret seeds per identity, run by us on a program that has never seen them"},
};
```

`(15)` is true and identical on both sides: 73 identity objectives × 15 episodes
each, and the hidden batch is 15 seeds. Same effort, different dungeons — which
is the whole point the label should make.

### 3.3 The sub-label is not decoration

"Self-reported" was a caveat. "Public Dungeons" is neutral, and sitting next to
"Private Dungeons" it reads as if the two differ only in seed choice. They also
differ in **who ran them**, and nobody has audited the public ones.

The `note` therefore has to appear somewhere that flips with the tier. This
also retires a real defect: captions today say `SELF-REPORTED*` and
`self-reported*`, but **nothing on the page ever defines that asterisk**.

**Amended during implementation.** This first shipped as a sub-label rendered
under each toggle. In review the author found a permanent line of prose under
all three switches too heavy, and it moved behind a `?` marker beside the
buttons, riding the page's existing `REFS`/`data-k` glossary popup so hover,
focus and click all behave like every other reference on the page.

The amendment satisfies the same requirement better than the original. The
defect being fixed was an *undefined* marker; a `?` that opens a titled
explanation is self-defining in a way the bare `*` never was. It also bought
room to say more than a one-line label allowed — the public entry now states
plainly that nobody re-runs the hacker's numbers, and the private entry states
that no one can write a program against seeds it cannot see.

### 3.4 Future compatibility

When public-seed verification arrives it is `tier=verified` under
`secret_fingerprint = sha256("public")` — the same tier, a different epoch. The
website's two axes then map cleanly onto (tier) × (secret fingerprint):

```
Public Dungeons (15)          Private Dungeons (15)
  self-reported                 verified · hidden epoch
  [later: verified · public]
```

`source_for` (§4) takes the epoch as an explicit argument for this reason, so a
second epoch is a caller change, not a rewrite. Building it now would be
speculative; the seam is what this spec commits to.

## 4. The epoch invariant and the row-source

### 4.1 The invariant

> A hidden-seed score must never be shown against a published-seed floor, and
> two epochs must never be mixed. Where the matching floor is absent, the
> product is **no Δ** — never a borrowed number.

A Δ across seed sets is not a weak measurement, it is a meaningless one. At time
of writing 5 identities have a private program result but no private floor yet;
those must show `—`.

### 4.2 `views/source.py`

`source_for(tier, epoch)` returns the atom table, the **matching** baseline
table, and the epoch predicate **together**:

| tier | atoms | baseline | extra WHERE |
|---|---|---|---|
| `self-reported` | `atoms` | `baseline_atoms` | `tier = ?` |
| `verified` | `verified_atoms` | `verified_baseline_atoms` | `secret_fingerprint = ? AND evaluator_image = ? AND seed IN (…)` |

Because the pair is handed out together, correct pairing is what a caller gets
by default: one `Source` yields both the scores and the floor that belongs with
them. Defeating it takes deliberately constructing a *second* `Source` and
cross-reading — so the invariant is a strong default, **not** an impossibility,
and code review still owes the both-tables call sites a look. Of the four views,
only `read_recognition` reads both, so that is the one place the check bites; it
resolves one `Source` and reuses it. A rotated secret, a re-pinned arena image,
or a retired seed all drop out in one place rather than at four call sites.

`source_for("verified", None)` — verifier not configured — raises; the API maps
it to 503, matching the rest of `/verify/*`.

### 4.3 Views routed through it

- `read_elites` — `FROM atoms WHERE tier = ?` becomes the source's table +
  predicate.
- `board` / `aggregate_board` — a single `store.iter_atoms(...)` call becomes the
  source's iterator.
- `read_recognition` — gains `tier`; both of its hardcoded reads (its
  `iter_baseline_atoms` call and its `WHERE a.tier = 'self-reported'` SQL) go
  through the source. **It also carries a latent invariant violation that this
  work must fix** — see §4.4.
- `read_baseline` — gains `tier`.

### 4.4 The `0.0` floor default must go

`views/recognition.py` reads its floor as `baseline.get(identity, 0.0)` in two
places (keepers at :95, breakthroughs at :123). On the self-reported tier the
AutoAscend floor covers every identity, so the default never fires and the bug is
invisible. On the private tier **5 identities currently have a program result and
no floor**, and the default would silently assert "the floor is zero" — inflating
that hacker's combined lift by the program's entire score and emitting a
breakthrough row claiming an advance past 0.0.

That is precisely the borrowed number §4.1 forbids, arriving through a default
rather than a join. **An identity with no matching floor is excluded from keepers
and from breakthroughs**, because a lift with no floor is not a small lift — it is
not a measurement. Absence must propagate as absence, never as zero.

`read_baseline` and `views/verified.py::_aggregate` are today the same
per-identity fold written twice, differing only by a seed filter. They collapse
into one helper — targeted cleanup in code this work already opens, not a
detour.

## 5. API surface

| endpoint | change |
|---|---|
| `GET /elites` | `?tier=verified` returns real rows instead of silently empty ones |
| `GET /board` | same |
| `GET /recognition` | gains `?tier=`, default `self-reported` |
| `GET /baseline` | gains `?tier=`, default `self-reported` |
| `GET /verify/overview` | **unchanged** |
| `GET /atoms` | **unchanged — deliberately** |

Every `?tier=` default stays `self-reported` for back-compat with existing
callers; the website always passes `tier` explicitly, so §6.1's private default is
a website behaviour and never a server-side one.

`?tier=verified` on a hub with no verifier configured → 503.

Two non-changes carry real weight:

- **`/verify/overview` is untouched.** `worker/verify.py:90` reads its `baseline`
  key to choose the next identity to floor. Reshaping it would stall
  verification.
- **`/atoms` must NOT route through `source_for`.** It returns raw per-atom rows
  **including `seed`**. Routing it through the source would publish the hidden
  seeds and void the whole tier. It keeps reading `atoms` directly, where
  verified rows structurally cannot appear. This is a security boundary, not an
  oversight.

## 6. Website

### 6.1 Three independent switches

Frontier, Frontier Keepers, and Greatest Breakthroughs each own a tier state,
each defaulting to `verified`, so a private keeper list can be read beside a
public breakthrough log. Each switch carries the §3.3 sub-label.

### 6.2 Deleting the hardcoded blanks

Three branches go: `loadUniverse()`'s `if(curTier==="verified"){UNIVERSE={};…}`,
`openIdentity()`'s `curTier==="verified"?{rows:[]}:…`, and `datum()`'s
`if(verified) return {state:'none',…}`.

Removing the third is what makes the agreed grid behaviour — **dim AutoAscend
floor where a private floor exists, blank where nothing has been measured** —
fall out of the existing self-reported code path rather than needing new code.
One rule, one implementation, both tiers.

Universe and baseline are fetched per tier and cached, so flipping does not
refetch.

### 6.3 Honest notes instead of a placeholder

The grid note replaces "M2b isn't live" with live coverage. The three cell states
are **mutually exclusive and must sum to 73** — a cell is led by a program, or
showing the dim floor, or blank:

- **led by a program** — a private result exists and clears the private floor,
  *or* clears nothing because no private floor exists yet (§4.1: value shown, Δ
  omitted);
- **at the AutoAscend floor** — a private floor exists and no private result
  beats it;
- **not yet measured** — neither exists.

Counts are computed from the loaded data, never hardcoded. Note that today the
floor identities are a strict *subset* of the identities with a program result
(every floored identity also has a private result), so the middle bucket is
populated only where a program fails to clear the floor — a state the held-out
finding says to expect, not an anomaly.

The drilldown's `Δ AA` column and its `autoascend / reference floor` row take the
tier-matching floor. Where the private floor is absent, the row is omitted and Δ
reads `—` (§4.1).

### 6.4 What "newest first" means on the private tier

Breakthroughs replay each identity chronologically by atom `created_at`. On the
public tier that is when the hacker's result landed — achievement order. On the
private tier `created_at` is when **our worker got around to verifying it**, so
the log is ordered by verification, not by achievement.

This is the only time we have, and it is not wrong — but it must not be presented
as an achievement timeline. Under the private tier the section description reads
*"ordered by when each result was verified"*, and a private breakthrough row means
"first verified result to clear the private floor on this identity", not "first to
get there".

### 6.5 Wording elsewhere

Captions that today read `SELF-REPORTED*` (hacker popup, registered-programs
table) take the new vocabulary. They get **no** switches — only Keepers and
Breakthroughs were asked for, and widening that is scope the request did not
ask for.

## 7. Testing

- **Backend units, per routed view:** epoch scoping (a wrong secret fingerprint,
  a wrong evaluator image, and a retired seed each drop out), and 503 when the
  verifier is unconfigured.
- **A regression test that `/atoms?tier=verified` still exposes no seeds** — §5's
  boundary, asserted rather than assumed.
- **Fixtures:** `load_fixtures` gains private-tier rows plus a private AA floor.
  Without them `make hub` serves an empty private tier and there is nothing to
  e2e against.
- **E2E against the local stack** (`make hub`), both tiers, all three switches.
- **Screenshots of both tiers** before handoff — layout defects do not show up in
  content assertions.

## 8. Decisions & scope

**Decided:**
- Website vocabulary is **Public Dungeons (15)** / **Private Dungeons (15)**;
  DB and API `tier` values are unchanged.
- The trust caveat moves out of the caption and into tier-flipping copy,
  replacing an asterisk the page never defined. **Amended in implementation**
  (§3.3): shipped as a `?` glossary marker beside each toggle rather than a
  permanent sub-label.
- **Three independent** tier switches (Frontier, Keepers, Breakthroughs).
- **Private is the default** everywhere, with no coverage threshold — the grid
  fills in as verification proceeds.
- Empty private cells **mirror the self-reported rule**: dim AA floor where a
  private floor exists, blank otherwise.
- The epoch invariant is defaulted structurally, in `source_for`, by handing out
  the atom and baseline tables as a pair. It is a strong default, not an
  impossibility: two separately-constructed `Source`s can still be cross-read, so
  `read_recognition` (the only view reading both tables) must resolve one
  `Source` and reuse it.
- Missing private floor produces **no Δ**, never a borrowed one; recognition's
  `baseline.get(identity, 0.0)` default is removed and such identities are
  **excluded** from keepers and breakthroughs (§4.4).
- Private breakthroughs are labelled as **verification-ordered**, not
  achievement-ordered (§6.4).

**Explicitly out of scope:**
- Renaming `tier` values, tables, routes, or verifier tokens.
- Any change to `/verify/overview` or `/atoms`.
- Switches on the hacker popup or registered-programs table.
- Public-seed verification (the `source_for` epoch argument is the seam; the
  second epoch is not built).
- Coverage-threshold gating of the default tier.
