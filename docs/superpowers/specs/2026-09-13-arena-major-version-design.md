# Arena Major Version — decoupling the verified tier from the image digest

**Status:** Design — ready for review
**Date:** 2026-09-13
**Extends:** `2026-09-03-verified-tier-design.md` (the verified tier and its epoch key) and
`2026-08-28-sandbox-image-distribution-design.md` (digest pins + the re-pin ceremony).
**Occasion:** PR #66 (`fix/arena-orphaned-child-processes`) carries a re-pin that, on deploy,
would empty the verified leaderboard.

**How to read this.** §2 is the mental model and §3 the glossary; every later section uses
those terms. §4 is the decisions ledger. §7 are the invariants — check any change against them.

---

## 1. Problem

The verified tier scopes every row to an **epoch**, and the epoch is
`(secret_fingerprint, evaluator_image)` where `evaluator_image` is the pinned arena image's
content digest (`store.py:76`, `:94`, `:112`). A submission is admitted only if its digest
equals the currently pinned one (`hub/verify.py:140`, `ParityMismatch`).

So the arena digest is doing a job it was never designed for: standing in for a **scoring
version**. Every rebuild of the arena image mints a new digest, and every new digest silently
starts a new epoch, orphaning everything verified under the old one — even when the rebuild
cannot possibly change a score.

That is not hypothetical. As of 2026-09-13:

| Ref | Arena digest | Status |
|---|---|---|
| `main` | `9b63a7b1…` | live in prod; holds the whole verified corpus |
| PR #66 | `d18bff83…` | re-pin from a process-lifetime fix |
| `codex/opencode2-integration` | `052453dc…` | a second, independent re-pin |

Prod holds, measured 2026-09-14: **37,470** verified participant rows over **36** programs from
**5** owners, a **1,095**-row AutoAscend hidden-seed floor, and **34** attempt records. That is
**38,599** rows in the three verified tables. Of the participant rows, **21,405** episodes over
**21** ranked programs are what the board currently displays; the remainder sit on seeds no longer
in the live hidden set, which is exactly the kind of history invariant I4 is written to preserve.
Either branch, on release, makes all of it unreachable. Re-earning the displayed part alone is
roughly five days of evaluator-node time.

The trigger is also mis-timed. Merging changes nothing; the reset fires on the **next release
tag**, so the person who causes it is whoever tags next, for any reason, possibly weeks later.

Root cause: there is **no scoring-version concept anywhere in the codebase**. The only version
constant that could be mistaken for one, `RUN_SCHEMA_VERSION`, is documented as the run-output
format and explicitly not scoring (`harness/version.py:12`, `diagnostics.py:47`).

## 2. Mental model (read first)

**Two different questions about an image, which the code currently conflates.**

- *Which exact bytes ran?* Answered by the content digest. Immutable, verifiable, and the right
  thing to record per row.
- *Are two runs comparable on one leaderboard?* A different question. Two images can differ in
  bytes and still score identically, which is precisely the case for a change to process
  lifetime, logging, or error messages.

This design keeps the digest answering the first question and introduces an **arena major
version** to answer the second. The digest stays on every row as provenance; the major becomes
the key that groups rows into a comparable set.

**The major is asserted, not proven.** A human decides whether a rebuild changes scores, and
records that decision as a number in source, reviewed in the PR that makes the rebuild. There is
no behavioral gate. That is a deliberate choice (D3) with a known failure mode (§8).

**Four version regimes, extending the three in the sandbox-image spec §2:**

| Regime | Identity | Changes when | Lives in |
|---|---|---|---|
| Package version | `0.23.5` | every release | `pyproject.toml` |
| Image content | digest `@sha256:…` | image inputs change | `_image_pins.py` (generated) |
| Run-output format | `RUN_SCHEMA_VERSION="v1"` | a run's publish format breaks | `harness/version.py` |
| **Arena major** | **`ARENA_MAJOR=1`** | **a rebuild changes scores** | **`arena_version.py` (hand-edited)** |

The arena major is orthogonal to the other three. A release does not bump it. A rebuild does not
bump it. Only a judgment that scores moved bumps it.

**This does not reverse D1 of the sandbox-image spec.** That decision said the *image* is
content-addressed rather than keyed to a version, and it still is. The major is not an image
name; it is a property *of* a digest, looked up through a map. Nothing addresses or pulls an
image by major.

**Nor does it contradict D4's "never gate on the digest."** That warning is about the
self-reported tier, where `evaluator_image` is client-supplied and fakeable. The verified tier is
the trusted path: its evidence comes from a verifier-token holder running on the evaluator node,
and the hub already gates on the digest there today. This design keeps that gate and only widens
what it admits.

## 3. Glossary

- **arena major** — an integer naming a set of arena digests declared to score alike.
- **the map** — `ARENA_MAJOR_BY_DIGEST`, digest → major, in `arena_version.py`.
- **current major** — `ARENA_MAJOR`, the major the hub reads and writes now.
- **classified** — a digest that appears in the map. An unclassified digest is rejected.
- **pooling** — two or more digests sharing one major, so their rows sit on one board.
- **epoch** — the old name for what is now `(secret_fingerprint, arena_major)`. Retained in this
  doc only where it names existing code.

## 4. Decisions ledger

Each: **decision — why — rejected alternative — consequence.**

- **D1. The verified key becomes `(secret_fingerprint, arena_major)`.** The digest answers "which
  bytes", not "comparable with what". *Rejected:* keeping the digest in the key and accepting a
  reset per rebuild (the status quo, which orphans all 38,599 verified rows per quality-of-life change).
  *Consequence:* a column swap plus a migration on three tables (§5.2).
- **D2. The digest column stays and is surfaced.** Provenance must survive; a row must still say
  which exact image produced it. *Rejected:* dropping it once it leaves the key. *Consequence:*
  the column is written on every insert and read back through the API (§5.5).
- **D3. The major is a hand-bumped constant, not a proven equivalence.** A behavioral gate needs
  golden per-seed data, a runner, and a policy for legitimate failures — machinery out of
  proportion to a decision made a few times a year. *Rejected:* a golden-replay gate that admits a
  digest only after it reproduces known results; also rejected: an allowlist with no current-major
  concept. *Consequence:* a wrong or forgotten bump silently pools incomparable scores (§8).
- **D4. The map lives in package source, not a hub table.** The classification is a judgment that
  belongs in code review, on the PR that rebuilt the image; and the worker and CLI need the same
  map, not just the hub. *Rejected:* a hub-side table editable without a deploy. *Consequence:*
  fixing a misclassification requires a release.
- **D5. The map is hand-edited and lives outside `_image_pins.py`.** That file is generated by
  `scripts/repin_images.py` and rewritten wholesale by the re-pin workflow, which would clobber
  anything it does not own. *Rejected:* extending the generated file. *Consequence:* a new module,
  and a re-pin leaves the map stale until a human edits it — which §5.4 turns into the forcing
  function.
- **D6. Admission accepts any classified digest at the current major.** A worker one release
  behind should keep working; the lockstep requirement between hub and evaluator node is the
  operational cost this design exists to remove. *Rejected:* keeping the strict equality check and
  using the major only for storage. *Consequence:* the parity-mismatch window during a deploy
  disappears; the hub must carry the map, not just its own pin.
- **D7. The existing corpus and PR #66's rebuild are both major 1.** The orphan fix changes
  process lifetime only and cannot alter an episode's outcome. *Rejected:* starting major 2 at
  PR #66 and accepting one last reset; also rejected: deciding after a two-digest replay.
  *Consequence:* the corpus survives; the equivalence claim rests on reading the diff rather than
  on measurement, which is recorded here as the risk it is (§8).
- **D8. The forcing function is a unit test, plus one guard step in the re-pin workflow.** The unit
  test asserting the pin is classified and current catches what a CI step would and runs on every
  developer machine rather than only on pull requests. It is not sufficient on its own, though:
  `sandbox-images.yml` commits the re-pin **straight onto the ref it was dispatched from, with no
  PR**. Dispatched from a feature branch the test failure rides that branch's PR, which is where
  the judgment belongs; dispatched from `main` there is no PR at all — only a red `main` nobody is
  watching, and `main` is not branch-protected, so it can still be tagged and released. That ships a
  hub on an unclassified pin, where every worker submission 400s and verification silently stops.
  So the re-pin workflow itself fails, immediately after committing the new pin, if
  `major_for(ARENA_IMAGE)` is `None`. *Rejected:* extending the image-pins staleness tripwire job
  (a different job, with its own rebuild semantics); also rejected: relying on the unit test alone
  and accepting the `main` gap. *Consequence:* one small step of YAML, and the forcing function is
  now real on both dispatch paths (§5.4).

## 5. Components

### 5.1 `src/nethackers/arena_version.py` — the map

A new hand-edited module in the package, beside the generated `_image_pins.py`:

```python
ARENA_MAJOR = 1

ARENA_MAJOR_BY_DIGEST = {
    "sha256:9b63a7b1fb11a82c01797a1099774b4e0ef6e321fbacd3a2256d8db6b4428142": 1,
    # pre-orphan-fix; the image the whole existing corpus was verified under
    "sha256:d18bff83ace72a35cbbfde29df8e2da73f6a4a7c2e48bbb0ac488ac9c45c12e3": 1,
    # PR #66 orphan fix — process lifetime only, no scoring change
}


def major_for(image: str) -> int | None:
    """The arena major for a full image ref, or None if unclassified."""
```

`major_for` takes the full ref as recorded in `Evidence.evaluator_image` and matches on its digest
portion, so a registry rename does not invalidate the map.

Each entry carries a one-line comment saying what the rebuild changed and why it did or did not
move scores. That comment is the audit trail for D3.

### 5.2 Storage and migration

The three verified tables swap `evaluator_image` out of their uniqueness keys for a new
`arena_major INTEGER NOT NULL`, keeping `evaluator_image` as a plain column:

| Table | Today | After |
|---|---|---|
| `verified_atoms` | `UNIQUE(solution_digest, identity, seed, secret_fingerprint, evaluator_image)` | `UNIQUE(solution_digest, identity, seed, secret_fingerprint, arena_major)` |
| `verified_baseline_atoms` | `UNIQUE(identity, seed, secret_fingerprint, evaluator_image)` | `UNIQUE(identity, seed, secret_fingerprint, arena_major)` |
| `verified_attempts` | queried by `(solution_digest, secret_fingerprint, evaluator_image)` | queried by `(solution_digest, secret_fingerprint, arena_major)` |

SQLite cannot alter a constraint, so this uses the rename-and-copy pattern the store already
follows (`_migrate_drop_objective_digest`, `store.py:191`): rename to `_old`, create the new
shape, copy rows through, drop. Added as `_migrate_add_arena_major` and called from
`init_schema` (`store.py:323`) alongside the existing migrations. Idempotent — a no-op on a fresh
or already-migrated database.

Backfill resolves each row's existing `evaluator_image` through the map. A row whose digest is
unclassified would have no major, and **what the migration does about that depends on what the row
is**:

- `verified_atoms` and `verified_baseline_atoms` are **scored data**, so I2 binds: the migration
  **raises**, refusing to place a row on a board it was never measured for. Both tables went
  through an admission check enforcing byte-equality with the pin, so every row in them was written
  under the single pinned digest and none should be unclassifiable.
- `verified_attempts` is an **audit record**, not scored data, so an unclassifiable row is
  **skipped**, counted and logged at WARNING. That table never had an admission check:
  `record_attempt` stored whatever `evaluator_image` the caller reported, and
  `eval/runner.py`'s `_default_image_digest` legitimately falls back to a bare image Id
  (`sha256:…` with no `@`) for a locally built arena, which `major_for` can never classify no
  matter what is added to the map. Any arena pin from before the current one lands there too.

The asymmetry exists because the blast radius does. `init_schema` runs in **every uvicorn worker at
boot**, so a raise here is not a loud failure on one request — it wedges the whole hub. Paying that
price to protect a scored row is right; paying it over an audit row that cannot move a board is not.
The migration returns both counts (`dropped` duplicates and `skipped` attempts) and `init_schema`
logs each at WARNING.

**Collisions.** Pooling two digests into one major can put two rows on the same
`(solution_digest, identity, seed, secret_fingerprint, major)`. None exist today, because nothing
has been verified under `d18bff83…` yet. The migration keeps the **earliest** row by `created_at`
and reports how many it dropped. Arbitrary but deterministic, and harmless given the two digests
are declared equivalent by D7.

### 5.3 Admission

`_check_hidden_evidence` (`hub/verify.py:130`) replaces its equality test with a map lookup:

- digest **not in the map** → `ParityMismatch`, naming the digest as unclassified;
- digest **classified at an older major** → `ParityMismatch`, naming that major and the current
  one, so the operator knows to upgrade rather than guessing;
- digest **classified at the current major** → accepted, whatever the current pin happens to be.

The eight `ARENA_IMAGE` call sites in `hub/api.py` pass `ARENA_MAJOR` where they passed the image,
and the resolved digest continues to flow through to the stored row.

`worker/verify.py` keeps running the pinned image (`:35`, `:100`) and keeps reporting the digest
it resolved (`eval/runner.py:225`). The worker gains no new knowledge; the hub does the mapping.

### 5.4 The forcing function

Two halves, because the re-pin can arrive two ways.

A unit test asserts the pinned `ARENA_IMAGE` appears in the map and carries `ARENA_MAJOR`. A
re-pin that nobody classified fails the suite immediately, and the fix is to add a line with an
explicit number — which is exactly the moment the judgment should happen. When
`sandbox-images.yml` was dispatched from a feature branch, that failure lands on the PR carrying
the re-pin commit, which is where it belongs.

But that workflow commits the re-pin **directly onto whatever ref it was dispatched from**, with no
PR (its own D11). From `main` the unit test therefore has nothing to block: it only turns `main`
red, `main` is not branch-protected, and a red `main` can still be tagged and released. So the
workflow carries a step of its own, straight after the re-pin commit, that fails when
`major_for(ARENA_IMAGE)` returns `None` — naming the file to edit and the decision to make
(does this rebuild move scores: join the current major, or start the next one?). The failure is on
the run that created the unclassified pin, not on a later, unrelated one.

Both halves deliberately force the judgment to be *made*, never to be *correct*. See §8.

### 5.5 What gets surfaced

- A verified `/board` or `/elites` response carries `arena_major` on its **envelope**, beside the
  `scope` and `tier` it already carries. Not on each row: every row in one response shares the
  scope by construction, so a per-row copy would be redundant. A self-reported response carries no
  such key at all, since `envelope()` drops `None` values.
- `GET /verify/overview` carries the current major and the digests classified under it.

Nothing carries a single digest per row. A program's ~1,000 verified episodes can legitimately
span several digests within one major, so a scalar there would be a lie. The digests belonging to
the major are reported once, at the overview level.

## 6. Rollout

1. Land this change on `main` and release it. The migration runs at hub startup; the board is
   unchanged, because every existing row backfills to major 1.
2. Then merge PR #66 and release. Its digest is already classified as major 1, so the board keeps
   its 21 rows and its floor.
3. `codex/opencode2-integration` classifies its own digest in the same PR that carries it.

Order matters. Releasing PR #66 before this change resets the tier, and no later change can undo
that from inside the database without re-verification.

## 7. Invariants (the self-consistency contract)

- **I1.** A verified row always records the exact digest that produced it, whether or not the
  digest is in the key.
- **I2.** No verified row is written without a classified major. An unclassified digest is
  rejected at admission, never stored with a guessed or default major.
- **I3.** Once any row exists under a digest, that digest's major is never edited. Editing it
  would silently move history from one board to another.
- **I4.** Bumping `ARENA_MAJOR` never edits or deletes rows. Old-major rows stay in the database,
  unreachable from the current board, and become reachable again if the major is restored.
- **I5.** Nothing outside the verified path consults the major. The self-reported tier stays
  unscoped, exactly as today.
- **I6.** Nothing resolves, pulls, or addresses an image by major. Images remain digest-addressed.

## 8. Known failure mode

D3 buys simplicity by trusting a human. Two ways it bites:

- **A forgotten bump.** Someone rebuilds with a change that does move scores and classifies it at
  the current major. The board then silently mixes incomparable results, and nothing detects it.
  The only mitigation here is the mandatory comment beside each map entry, and review of the PR
  that adds it.
- **This design's own first entry.** D7 pools `9b63a7b1…` and `d18bff83…` on the strength of the
  source diff being process-lifetime only. The rebuild also re-resolved whatever is unpinned in
  the image inputs, so byte-level equivalence is not established. A replay of one existing elite
  under both digests would settle it and is cheap; it is out of scope here by decision, and this
  paragraph exists so the assumption is on the record rather than implied.

A third, smaller one, on the migration rather than the design:

- **"No unclassifiable rows exist today" is now a measured fact, not an inference.** It began as
  an inference from the admission check, sound for the two atom tables that check guarded and
  simply wrong for `verified_attempts`, which had no such check — hence the split in §5.2. A
  read-only query against the live database on **2026-09-14** settled it: all three tables hold
  exactly **one** distinct `evaluator_image`, and it is
  `sha256:9b63a7b1…`, already classified at major 1. So the backfill classifies every existing row,
  the hard raise cannot fire on this database, and the `verified_attempts` skip path exists for
  future rows rather than present ones. The split in §5.2 stays regardless: it is the right shape
  for a table whose write path never validated the image, and it costs nothing when unused.

A behavioral gate (the rejected alternative in D3) is the real answer to both, and remains the
natural follow-on if the failure mode ever bites.

## 9. Testing

- `arena_version` — the pinned digest is classified and at the current major (§5.4); `major_for`
  matches on the digest portion and returns `None` for an unknown one.
- Admission — a classified digest at the current major is accepted; an unclassified one raises
  `ParityMismatch`; a classified one at an older major raises `ParityMismatch` naming both majors.
- Migration — an old-shape database holding rows under two digests migrates to one major, with the
  collision resolved to the earliest row and the dropped count reported; running it twice is a
  no-op.
- Boards — rows produced under two digests inside one major count as one program's coverage, not
  two.
- Surface — a verified board row exposes `arena_major`; the overview exposes the current major and
  its classified digests.

## 10. Open decisions

None. D7 records the one assumption that a cheap experiment could still overturn (§8).
