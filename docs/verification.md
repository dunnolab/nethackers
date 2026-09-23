# Public and Private Dungeons

Every registered program carries two scores. The public one is the author's
own run on 15 published seeds per identity, and anyone can reproduce it. The
private one is our verifier's run on seeds nobody has seen, on our hardware.
You can check any public number yourself; for the private one you trust us
to hold the secret, run the pinned image, and publish only aggregates. The
loop that chases the public score is in [harness.md](harness.md).

## The two tiers

| | Public Dungeons | Private Dungeons |
|---|---|---|
| seeds | 15 published per identity | secret; 15 per identity in production |
| run by | the author, on their machine | our verifier, on our hardware |
| answers | how good is the bot on seeds it could tune against | does that transfer |
| reproducible by you | yes, given a deterministic bot | no |
| `tier` in the API | `self-reported` | `verified` |

The website shows Private first.

## Where private seeds come from

Every game's seeds are derived (`arena/seeds.py`):

```
digest = HMAC-SHA256(secret, "nethack-arena\0" + evaluation_id + "\0" + trajectory_id)
core, display, level and bot seeds = four 63-bit slices of the digest
```

The public secret is the literal string `"public"`, which is what makes
`nethackers eval` the same game on every machine. The private secret lives
only in the hub's and the verifier's environment
(`NETHACKERS_HIDDEN_SECRET`) and in no image. Under a different key the same
constants yield unrelated games, so the secret is the only thing that
separates public games from private ones. Its fingerprint, `sha256(secret)`,
travels with every verified submission, so the hub can reject work computed
under a stale secret without revealing the secret.

## How a private score is made

1. The verifier pulls the program at its registered `repo@commit`.
2. It runs the pinned `linux/amd64` arena image: the same container and the
   same code you run.
3. Every identity, 73 × 15 episodes, one pass.
4. Each identity's results go to the hub as soon as that identity finishes;
   a crash at identity 50 keeps the 49 already banked.
5. The hub stores them under `(secret fingerprint, arena major, seed)`.

One run, no averaging, no re-runs. A program that cannot be cloned or
crashes on load fails the same way next time and is not retried; a hub
outage during submission is.

## Check it yourself

```bash
nethackers show <program-id>                                   # its repo@commit and both scores
nethackers pull github.com/<owner>/nethacker@<commit> ./check   # the exact tree
nethackers eval ./check --objective val-dwa-law-fem            # the public seeds, on the pinned image
```

## What you trust us for

- The private secret stays private, and is rotated if it doesn't.
- The verifier runs the pinned image, unmodified.
- Seeds never leak, including through per-seed results.
- The AutoAscend baseline was measured the same way.
- A delta is only ever computed within one epoch.

## The AutoAscend baseline

`nethackers-worker --baseline` scores AutoAscend on the same private seeds
and stores the result apart from participants. The site's delta is against
that number. It is a reference, not a floor: bots do score below it.

## What you can see

Per-identity aggregates: mean progression, deepest milestone, episode count.
Never per-seed rows and never seed ids; publishing them, even implicitly,
would make the private tier a public one. Per-program coverage, atoms out of
73 × 15, leaves the hub only through token-gated routes.

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
| the arena image is re-pinned without a bump | nothing |

Re-measuring takes about five days of verifier time at the corpus size the
major was introduced with, about 21,400 participant episodes plus the
1,095-episode baseline, and the delta column reads `—` until the baseline is
redone too.

## What this does not guarantee

- A private number is 15 games per identity: a sample, with the noise that
  implies.
- Verification runs the code; it does not review it.
- A public number is the author's claim about their own machine.
- All games are `linux/amd64` games. Other architectures play different
  games and are refused.
- A strong public score with a weak private one is information, not an
  accusation.

## FAQ

**Why is my private score lower than my public one?** The public seeds were
there to tune against and the private ones were not. The gap is what the
tier measures.

**Why isn't my program verified yet?** The verifier takes the least-covered
program first and runs one program at a time, hours each. Coverage shows on
the site.

**Can I see which private seed my bot died on?** No. Per-seed results would
publish the seeds.

**Who holds the secret?** The hub and the verifier, in their environment. It
is in no image, no repository and no log.

**Can I run the verifier?** It needs the secret and a trusted token, so no.
Operating it is in [verifier.md](verifier.md).
