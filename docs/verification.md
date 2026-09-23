# Public and Private Dungeons

Every registered program has a public score, and a private one once our
verifier has run it. The public one is the author's own run on 15 published
seeds per identity, and anyone can reproduce it. The private one is our
verifier's run on seeds nobody has seen, on our hardware. You can check any
public number yourself; for the private one you trust us to hold the secret,
run the pinned image, and publish only aggregates. The loop that chases the
public score is in [harness.md](harness.md).

## The two tiers

| | Public Dungeons | Private Dungeons |
|---|---|---|
| seeds | 15 published per identity | secret; 15 per identity today (hub configuration, not code) |
| run by | the author, on their machine | our verifier, on our hardware |
| answers | how good is the bot on seeds it could tune against | does that transfer |
| reproducible by you | yes, given a deterministic bot | no |
| `tier` in the API | `self-reported` | `verified` |

The website opens on Private.

## Where private seeds come from

Every game's seeds are derived (`src/nethackers/arena/seeds.py`):

```
digest = HMAC-SHA256(secret, "nethack-arena\0" + evaluation_id + "\0" + trajectory_id)
core, display, level and bot seeds = four 63-bit slices of the digest
```

`evaluation_id` is the constant `"local"` on every scoring path, and the
published "seeds" 0 to 14 are trajectory ids. The public secret is the
literal string `"public"`, which is what makes `nethackers eval` the same
game on every machine that runs the pinned amd64 image. The private secret
lives in the hub's environment (`NETHACKERS_HIDDEN_SECRET`); the verifier
holds a token and fetches the secret and the seed list from the hub on each
pass, keeping them in memory. Under a different key the same trajectory ids
yield unrelated games. The private tier has its own key and its own
trajectory ids, and both stay on the hub. The key's fingerprint,
`sha256(secret)`, travels with every verified submission, so the hub can
reject work computed under a stale secret, or from an image it has not
classified, without revealing anything.

## How a private score is made

1. The verifier pulls the program at its registered `repo@commit`.
2. It runs the pinned `linux/amd64` arena image, the same container and code
   you run; the hub refuses evidence from any digest it has not classified.
3. Every identity, 73 × 15 episodes, one pass.
4. Each identity's results go to the hub as soon as that identity finishes;
   a crash at identity 50 keeps the 49 already banked. The program then
   stays at 49 of 73 until an operator re-runs it by hand, and it is ranked
   on the Private board with what it has.
5. The hub stores them under `(secret fingerprint, arena major, seed)`.

One run, no averaging, no re-runs. A program that cannot be cloned, or whose
arena run fails on any identity, is marked `build_failed` or `crashed` and
is not offered to the verifier again in this epoch; an operator can re-run
it by hand. A hub error during submission is `infra_error` and is retried.

## Check it yourself

```bash
nethackers show <program-id>                                   # its repo@commit and owner
nethackers pull github.com/<owner>/nethacker@<commit> ./check   # the exact tree
nethackers eval ./check --objective val-dwa-law-fem            # the public seeds, on the pinned image
```

Both scores are on the site, and in the API: `/board?tier=self-reported`
and `/board?tier=verified`.

## What you trust us for

- The private secret stays private, and is rotated by hand if it doesn't.
- The verifier runs the pinned image, unmodified.
- Seeds never leak, including through per-seed results.
- The AutoAscend baseline was measured the same way.
- A delta is only ever computed within one epoch.

## The AutoAscend baseline

`nethackers-worker --baseline` scores AutoAscend on the same private seeds
and stores the result apart from participants. The site's delta is against
that number, and the site calls it the floor; bots do score below it.

## What you can see

`GET /verify/overview` is public: per-identity aggregates, mean progression,
deepest milestone and episode count, with the baseline beside them. The
boards and elites take `?tier=verified` and show each program's verified
mean per identity, and how many identities it has been verified on. Never
per-seed rows and never seed ids; publishing them, even implicitly, would
make the private tier a public one. Per-program coverage in atoms leaves the
hub only through the verifier-token routes, and a program's own popup on
the site shows public numbers only.

## What resets the private board

Every verified atom is scoped by `(secret fingerprint, arena major, seed)`.
`ARENA_MAJOR` names a set of image digests declared to score alike
(`src/nethackers/arena_version.py`), so a rebuild that changes logging or
packaging keeps the corpus.

| Event | What you see |
|---|---|
| the private secret is rotated | every private number disappears until re-measured |
| `ARENA_MAJOR` is bumped | the same; the previous major's atoms stop being read, nothing is deleted |
| a seed is retired from the list | that seed drops out of the aggregates |
| the arena image is re-pinned without a bump | nothing, once the new digest is classified at the current major; until then the hub answers the verifier with 400 and nothing new lands |

Re-measuring runs the whole grid again: every program on the board, 73
identities × 15 seeds each, plus the 1,095-episode baseline, one program at
a time. The delta column reads `—` until the baseline is redone too.

## What this does not guarantee

- A private number is 15 games per identity: a sample, with the noise that
  implies.
- Verification runs the code; it does not review it.
- A public number is the author's claim about their own machine.
- All games are `linux/amd64` games; an arm64 host plays them under
  emulation. A build for another architecture plays different games, and
  the hub refuses its evidence.
- A strong public score with a weak private one is information, not an
  accusation.

## FAQ

**Why is my private score lower than my public one?** The public seeds were
there to tune against and the private ones were not. The gap is what the
tier measures.

**Why isn't my program verified yet?** The verifier takes the least-covered
program first and runs one program at a time. Your program appears on the
Private board once its first identity lands and moves up as coverage grows;
a partial program never outranks a full one. The count is in the API,
`coverage` out of `identities_total` on `/board?tier=verified`.

**Can I see which private seed my bot died on?** No. Per-seed results would
publish the seeds.

**Who holds the secret?** The hub, in its environment on the VM. The
verifier fetches it from the hub with its token at the start of a pass and
keeps it in memory; the arena container only ever sees the seeds derived
from it. It is in no image, no repository and no log.

**Can I run the verifier?** The command ships with the package, but it needs
a verifier token, and the hub hands the secret only to a token it knows. So
no. Operating it is in [verifier.md](verifier.md).
