# Operating the verifier

`nethackers-worker` produces the Private Dungeons scores. It needs the hub's
private secret and a trusted token, so it runs on hardware we control and is
not part of the participant CLI. What its numbers mean is in
[verification.md](verification.md); the hub it reports to is in
[../deploy/README.md](../deploy/README.md).

## Run it

```bash
nethackers-worker --token "$TOKEN"                                     # daemon: fetch, verify, repeat
nethackers-worker --once --token "$TOKEN"                              # one candidate pass, then exit
nethackers-worker github.com/owner/nethacker@<sha> --token "$TOKEN"    # one program, then exit
nethackers-worker --baseline --tree roots/autoascend --token "$TOKEN"  # the AutoAscend baseline
```

`--max-parallel-evals` caps the episodes one eval runs at once. Unset, it is
one per CPU the container runtime has, bounded by its memory (about 1 GiB
per episode within three quarters of it) and by the batch of 15. More is not
faster: oversubscription contends for CPU, and the arena's per-action
timeout is wall-clock, so contention lowers the scores themselves.

## What it does

1. `GET /verify/config`: the secret and the seed list.
2. `GET /verify/candidates`: up to `--limit` programs (default 8), least
   covered first. Fully covered programs are skipped, and so are programs
   whose last attempt failed deterministically (`build_failed`, `crashed`,
   `hung`); an `infra_error` stays retryable.
3. For each: pull at `repo@commit`, evaluate all 73 identities through the
   ordinary arena path, submit each identity's atoms as it finishes.
4. An empty list sleeps 30 s. A hub error backs off from 10 s, doubling to
   600 s, and resets on any success.

There is no queue and no lease. A verifier that dies mid-program loses that
program's remaining identities and nothing else; the next pass recomputes
the work list from what the hub already stores.

Two edges:

- The backoff guards the two GETs. If the attempt-reporting POST fails
  deterministically, schema skew say, the attempt is never recorded, the
  same candidates come back, and the loop spins at clone speed.
- The hub client is built with an explicit 60 s timeout. httpx's 5-second
  default silently dropped a POST carrying a finished identity's 15 atoms.

## The baseline

`--baseline` takes a local tree, not a `repo@commit`: the baseline has no
`solutions` row and must never get one. It resumes, skipping identities the
hub already holds a complete batch for and redoing a partial identity in
full. It keeps no attempt bookkeeping; a failure is a non-zero exit and a
log line. Results go to their own table, read by their own function, so
nothing filters the baseline out of the participant aggregate.

## After a re-pin

A new arena digest is unclassified until it is added to
`ARENA_MAJOR_BY_DIGEST` in `src/nethackers/arena_version.py`, and the hub
answers submissions from an unclassified digest with a 400. Classifying is a
step in [releasing.md](releasing.md): the current major when the rebuild
cannot move a score, a new major when it can. A bump touches no rows; the
previous major's atoms stop being read, and the corpus is re-earned by
running the verifier over the whole grid again, about five days at the size
it had when the major was introduced.
