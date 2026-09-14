# amd64 Reference Architecture — arena major 2 and the network reset

Status: approved design, not yet implemented.
Supersedes invariant **I5** of `2026-09-13-arena-major-version-design.md` (see §7).

## 1. Problem

The arena is deterministic only *within* one CPU architecture. The same
`(seed, character)` plays a different game of NetHack on `linux/amd64` and
`linux/arm64`, from the same image build, the same NLE 1.3.0 and the same gcc.

Root cause, verified with a standalone C reproduction compiled in both arena
images: NetHack calls two RNG-consuming helpers as sibling function arguments,
for example `mkgold(0L, somex(croom), somey(croom))` at `src/mklev.c:831`.
`somex`/`somey` (`src/mkroom.c:640-651`) each call `rn1() -> rn2()` and advance
the core RNG. C leaves the evaluation order of function arguments *unspecified*,
and gcc 14.2.0 evaluates left-to-right on aarch64 and right-to-left on x86-64,
so the two coordinates receive swapped draws. Confirmed against gcc's own
`-fdump-tree-gimple` output on both targets: only call-argument lists differ in
order; binary operators, subscripts, assignments and initializer lists are
left-first on both.

Measured impact on one leaderboard entry, the monk elite
(`vkurenkov/nethacker@025d63f`, identity `mon-hum-neu-mal`, the published
15-seed batch):

| architecture | mean progression |
| --- | --- |
| arm64 | 0.15494 |
| x86_64 | 0.10994 |

The x86_64 figure matches, to three decimals, the number a contributor
independently reported when they could not reproduce the board.

The board is already mixed and cannot tell. Atoms record `evaluator_image` and
nothing else about the machine, and the evolve path writes a *mutable tag*
(`harness/evaluate.py` passes `image_digest_resolver=lambda img: img`), so
99.2% of public atoms say only `nethackers/arena:dev`. Architecture was
recoverable only by behavioural fingerprinting: of 246 monk seed-5 atoms, 232
carry the arm64 signature, 3 the x86_64 signature, and 11 are programs where the
fingerprint does not apply.

Every published score is therefore conditional on whose machine produced it.
That invalidates the public board, the AutoAscend floor (computed on arm64) and
any cross-contributor comparison.

## 2. Mental model (read first)

Three ideas, in order.

**The architecture is a property of the pinned bytes, not of a runtime flag.**
An amd64-specific *manifest* digest runs x86_64 on an arm64 host with no
`--platform` argument at all (verified). So declaring the reference architecture
is a change to what `ARENA_IMAGE` names, and nothing else. This keeps invariant
I6 of the prior spec intact: images stay digest-addressed, and nothing resolves
an image by major.

**The reset is already built, for one tier.** `Epoch` is
`(secret_fingerprint, arena_major, seeds)`. Invariant I4 of the prior spec says
bumping `ARENA_MAJOR` never edits or deletes rows: old-major rows stay in the
database and become unreachable. Bumping to 2 therefore empties the verified
board and re-queues the worker with zero migration. That *is* the network
restart.

**The public tier has no such mechanism, so it gets a one-time genesis.** It is
unscoped by design (prior spec I5). Rather than teach it about majors for
reading, the live public tables are archived wholesale and recreated empty, and
admission is tightened so they cannot re-mix. Reading stays unscoped; writing
does not.

## 3. Glossary

- **arena major** — integer naming a set of arena image digests declared to
  score alike. Defined in `src/nethackers/arena_version.py`.
- **index digest** — the digest of a multi-arch OCI image index. Resolves to
  whichever platform the host is.
- **manifest digest** — the digest of one platform's image. Resolves to that
  platform on every host, emulating if necessary.
- **genesis** — the one-time archival of the live public tables and their
  recreation as empty tables. Not a wipe: rows are retained, unreachable.
- **classified digest** — one present in `ARENA_MAJOR_BY_DIGEST`. A tag is
  unclassified by construction.

## 4. Decisions ledger

- **D1. amd64 is the reference architecture; every other host emulates.**
  Chosen over arm64 despite 232 of 246 sampled atoms being arm64, because the
  verifier, all CI runners and any rented server are x86-64, and because
  contributors on servers are expected to grow. The existing board is discarded
  either way (D3).
- **D2. The reference is expressed by pinning the amd64 manifest digest.** Not
  by adding `--platform` to the run site. The pin is the single place the
  architecture is decided, it needs no new flag, and it makes an arm64-only
  image fail the existing presence and pull checks by construction rather than
  by new platform-awareness in three call sites. `--platform linux/amd64` is
  still passed at the run site, but only to suppress the mismatch warning Docker
  prints on every emulated run. It is cosmetic and carries no correctness
  weight; the pin alone is load-bearing.
- **D3. This is a full genesis, including the program registry.** Atoms,
  programs, baselines, lineage and attainment are all archived. Contributors
  re-register. Rejected: keeping programs and gating the verification queue,
  which leaves 551 stale candidates against a verifier measured at roughly four
  programs a day.
- **D4. Archived rows are unreachable through the API.** No endpoint learns
  about the `*_v1` tables. Rejected: a `?tier=legacy` path and a first-class
  legacy board, both of which keep two boards to explain.
- **D5. Public admission requires a classified digest at the current major.**
  This *changes prior-spec I5*. Without it, genesis buys an empty board that
  immediately re-mixes, since a locally built tag can self-report today. The
  verified tier already applies exactly this rule.
- **D6. The image ladder inverts: the pin wins by default, everywhere.**
  `resolve_image` currently returns the local dev tag inside a repo checkout.
  That is the mechanism by which Mac checkouts silently scored on arm64. The
  local tag becomes opt-in via the existing explicit flag or environment
  variable, for arena development only.
- **D7. The hidden secret does not rotate.** The major bump alone rotates the
  epoch. Rotating adds a coordinated hub-and-worker change for no extra
  isolation.
- **D8. The nineteen argument-order hoists are out of scope.** NLE ships
  prebuilt manylinux wheels, so patching means carrying a source-built fork:
  every base build compiles NetHack, and a patch is maintained against
  upstream. Deferred to its own major bump. Consequence: the C-level divergence
  remains, and amd64-as-reference is the only thing holding scores together.
- **D9. `ubirthday` is out of scope.** It is wall-clock seeded
  (`src/u_init.c:649,651`) and leaks into seeded games, but the fix belongs
  upstream: NLE PR #130 implements it and is open, mergeable, blocked only on
  named constants and a test. Carrying it locally would contradict the project's
  preference for staying faithful to upstream NLE.
- **D10. The mutator image stays native and multi-arch.** It runs the coding
  agent, not scoring, so its architecture cannot move a score. Forcing it to
  emulate would slow the agent for nothing.
- **D11. No architecture column on atoms.** Enforced admission (D5) makes every
  public atom the pinned amd64 image by definition, so the major already carries
  that meaning.
- **D12. Genesis is an explicit one-shot command, not a boot migration.** The
  prior spec's migration ran on boot because it added a column. This one empties
  the live board, and hub deploys auto-roll-back on a failed health check, which
  would leave archived tables and code that knows nothing about them.

## 5. Components

### 5.1 `src/nethackers/arena_version.py`

`ARENA_MAJOR` becomes `2`. `ARENA_MAJOR_BY_DIGEST` gains one entry at major 2,
carrying the comment the module mandates:

```
# Reference architecture pinned to linux/amd64 (design 2026-09-14). Scores move:
# NetHack consumes RNG in unsequenced sibling call arguments, and gcc orders
# those arguments differently on x86-64 and aarch64, so the same seed generates
# a different dungeon per architecture. Not comparable with major 1, which was
# scored on whatever architecture each contributor happened to run.
"sha256:862434c5e719941a3ef63a01449dfe4ef7c158790b15ab9628d39ecd336796eb": 2,
```

The two major-1 entries stay. Per I3 they are never edited.

### 5.2 `src/nethackers/_image_pins.py`

`ARENA_IMAGE` becomes the amd64 manifest digest above, the `linux/amd64` leg of
the current index digest `d18bff83…`. `MUTATOR_IMAGE` is untouched (D10).

This file is generated by `scripts/repin_images.py`. That script and
`.github/workflows/sandbox-images.yml` must be changed to emit the arena's
platform-specific leg rather than the index digest, or the next re-pin silently
reverts the reference architecture. This is the single highest-risk item in the
change.

### 5.3 Shared admission

`hub/verify.py` already raises `ParityMismatch` when a digest is unclassified
*or* classifies to a different major. Lift that test into one helper in
`hub/validate.py` — the module both admission paths already import, and a
hub-side concern rather than an arena-side one, so `arena_version.py` stays the
pure map it is today (prior spec D4). `hub/verify.py` calls the helper and keeps
raising `ParityMismatch` for its own callers. `hub/validate.py` currently checks
only that `evidence.evaluator_image` is non-empty (`MissingImage`); it gains the
same rule under a new error type so the two tiers' failures stay
distinguishable in logs.

The error text is the contributor-facing surface of this whole change. It must
distinguish the two cases and name the action:

- unclassified: the evidence came from an image this hub does not recognise,
  typically a locally built tag. Action: stop overriding the arena image.
- wrong major: the evidence came from a retired arena. Action: upgrade the CLI.

### 5.4 `harness/sandbox_preflight.py`

`resolve_image` drops the repo-checkout branch for the arena: explicit value
wins, otherwise the pin. The local dev tag is reachable only through the
explicit flag or `NETHACKERS_ARENA_IMAGE`.

No other change is needed for platform correctness. Because the pin now names
one platform's bytes, `image_present`, `_pull_image` and digest resolution all
operate on that platform by construction.

### 5.5 Genesis migration

A one-shot command, run against the deployed hub after a database backup.

Renames `solutions`, `atoms`, `baseline_atoms`, `lineage`, `attainment` and
`attainment_holders` to `*_v1`, then recreates each from the existing DDL.
`poll_votes` is unrelated and untouched. The verified tables are untouched: I4
handles them.

Properties: one transaction; idempotent, guarded on a marker rather than on
`ARENA_MAJOR` so a rollback and redeploy cannot re-fire it; logs the row count
moved per table, matching the arena-major migration's precedent.

### 5.6 `doctor`: the Rosetta advisory

A new check, `status="warn"` and `severity="soft"` when Rosetta is off. By
prior-art invariant INV6 a warning never gates any capability, so `doctor`'s
exit code — the onboarding oracle — cannot move.

Fires only on macOS with an arm64 host. Detection is host-side: read
`UseVirtualizationFramework` and `UseVirtualizationFrameworkRosetta` from Docker
Desktop's settings store. **Do not probe inside the container**: the Rosetta
mount is absent there even when Rosetta is active, so an in-container probe
reports the wrong answer silently.

When the runtime is not Docker Desktop, or the settings file is unreadable, the
check reports unknown rather than disabled. Colima's equivalent is the
`--vz-rosetta` flag the codebase already references.

The message carries the measurement, because the number is what makes someone
act: the same 15-episode batch on the same machine took 823s under QEMU and
224s with Rosetta.

`src/nethackers/doctor.schema.json` and `tests/test_doctor_schema.py` gain the
new check, or the committed-schema drift gate fails.

### 5.7 Documentation

`README.md` and `docs/harness.md` state that amd64 is the scoring architecture,
that other hosts emulate, and that Apple Silicon should enable Rosetta.

`docs/harness.md` currently tells readers they may build the arena image
themselves. Under D5/D6 that no longer produces registrable evidence and the
passage must be corrected.

## 6. Rollout

1. Release the CLI carrying §5.1-§5.4, §5.6, §5.7.
2. Back up the hub database.
3. Deploy the hub. It begins rejecting major-1 evidence immediately.
4. Run genesis (§5.5) at once, so the window in which the board shows major-1
   numbers nobody can add to stays short.
5. Recompute the AutoAscend floor on the amd64 image and insert it.
6. The verified worker re-queues by itself under the new epoch.

Contributors on older releases start failing at register the moment step 3
lands. This needs an announcement, and it is why §5.3's error text matters.

## 7. Invariants

Carried forward from the prior spec: I1, I2, I3, I4, I6 are unchanged.

**I5 is superseded.** It read: *nothing outside the verified path consults the
major; the self-reported tier stays unscoped.* It becomes:

- **I5′.** Self-reported *reads* stay unscoped by major. Self-reported *writes*
  require a classified digest at the current major. Reading a tier never
  consults the major; admitting evidence to any tier always does.

New:

- **I7.** `ARENA_IMAGE` names a platform-specific manifest digest, never an
  index digest. A re-pin that emits an index digest is a defect.
- **I8.** Genesis runs at most once per database. It archives; it never deletes.
- **I9.** The Rosetta check never changes `doctor`'s exit code, on any platform.

## 8. Known failure modes

**The re-pin workflow reverts the architecture.** `scripts/repin_images.py`
regenerates `_image_pins.py` wholesale. If it is not taught to emit the amd64
leg, the next re-pin restores an index digest, which is unclassified, and
registration fails globally until someone notices. Mitigation: the existing
re-pin guard step, extended to fail on an index digest (I7).

**Genesis runs, then the deploy rolls back.** Mitigated by D12 (explicit
command, after the health check has already passed) and by the backup in step 2.

**An arm64 contributor sees evaluation slow down.** Expected, and the reason
§5.6 exists. Measured on one machine, one 15-episode identity batch. Rosetta
against QEMU is a like-for-like comparison, same games and the same step count:
224s against 823s, so 3.7x. Rosetta against native arm64 is *not* like-for-like,
because the amd64 dungeon set is a different set of games needing about a third
fewer steps; the honest figure there is throughput, 2,137 steps per second
against 4,176, so roughly 2x, which shows up as 224s against 179s in wall clock.

**The board stays empty for a while.** Genesis discards 551 programs and 56,340
atoms. There is no automatic refill: contributors must re-register.

## 9. Testing

- Admission, table-tested across classified / unclassified / wrong-major, for
  both tiers, since they share one helper.
- `resolve_image`: in-repo returns the pin; an explicit value still wins.
- Genesis: idempotency across two runs, atomicity on a mid-migration failure,
  correct row counts logged, verified tables untouched.
- `doctor`: macOS-arm64, macOS-Intel, Linux and unreadable-settings, asserting
  the exit code is identical in all four.
- The committed doctor schema and its drift test.
- One pass against the local stack: evidence from the pinned digest registers;
  evidence from a tag is refused with the unclassified message.

## 10. Out of scope

The nineteen argument-order hoists (D8), `ubirthday` (D9), an execution digest
for the partial-agreement verification gate, and moving the verifier to arm64
hardware.
