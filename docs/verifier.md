# Operating the verifier

`nethackers-worker` produces the Private Dungeons scores. It ships in the
same package as a second command, but it needs a verifier token, and the hub
hands the private secret only to a token it knows; without one every
`/verify` route answers 401. So it runs on hardware we control. What its
numbers mean is in [verification.md](verification.md); the hub it reports to
is in [../deploy/README.md](../deploy/README.md).

## Run it

```bash
export NETHACKERS_VERIFIER_TOKEN=…                                    # or --token on each command
nethackers-worker --hub https://nethackers.dunnolab.ai                # daemon: fetch, verify, repeat
nethackers-worker --hub https://nethackers.dunnolab.ai --once         # one candidate pass, then exit
nethackers-worker --hub https://nethackers.dunnolab.ai github.com/<owner>/nethacker@<sha>   # one program
nethackers-worker --hub https://nethackers.dunnolab.ai --baseline     # the AutoAscend baseline
```

`--hub` defaults to `http://localhost:8000`. Spell a program the way the hub
stores it, `github.com/<owner>/<name>@<sha>`; a bare `owner/name` clones
and then 404s at submission. `--baseline` takes the packaged AutoAscend tree
by default (`--tree` for another).

`--max-parallel-evals` caps the episodes one eval runs at once. Unset, it is
one per CPU the container runtime has, bounded by its memory (about 1 GiB
per episode within three quarters of it) and by the batch, one episode per
hidden seed; 8 when the runtime cannot be asked. More is not faster:
oversubscription contends for CPU, the arena's per-action timeout is
wall-clock, so contention lowers the scores themselves, and an episode that
runs out of memory scores 0 (asking for more than the box holds prints a
warning and runs anyway).

The daemon prints nothing of its own. What you see is the arena container's
stderr, one line per episode, which never names the program. Progress per
program is on the hub: `GET /verify/overview` and, with the token,
`GET /verify/candidates`.

## What it does

1. `GET /verify/config`: the secret and the seed list, kept in memory.
2. `GET /verify/candidates`: up to `--limit` programs (default 8), least
   covered first. Fully covered programs are skipped, and so are programs
   whose last attempt was `build_failed` (could not be cloned) or `crashed`
   (the arena failed on any identity, a transient host hiccup included); an
   `infra_error` (the hub failed during submission) stays retryable.
3. For each: pull at `repo@commit`, evaluate all 73 identities through the
   ordinary arena path, submit each identity's atoms as it finishes.
4. An empty list sleeps 30 s. A hub error on those two GETs backs off from
   10 s, doubling to 600 s, and resets when a fetch succeeds. `--once` does
   the same, so it does not exit on a down hub.

There is no queue and no lease. A verifier that dies mid-program keeps what
it already submitted; the hub holds it. The next pass offers the program
again, behind every uncovered one, and runs it from the first identity; rows
the hub already holds are recomputed and dropped on insert. Only
`--baseline` resumes per identity. A `crashed` program leaves the queue at
whatever coverage it reached and is ranked on the Private board with that,
until an operator re-runs it by hand with the one-program form. Two nodes
can run at once: both take the same candidates, and the second insert is a
no-op.

Two edges:

- The backoff guards the two GETs. If the attempt-reporting POST fails
  deterministically, an unclassified digest say, no attempt is recorded and
  a failed program comes straight back: at clone speed for a build failure,
  at one identity's eval otherwise. The daemon prints nothing about it.
- The hub client is built with an explicit 60 s timeout. httpx's 5-second
  default once cut a POST carrying a finished identity's 15 atoms, and the
  log said only `timed out`.

## The baseline

`--baseline` takes a local tree, not a `repo@commit`: the baseline has no
`solutions` row and must never get one. It resumes, skipping identities the
hub already holds a complete batch for and redoing a partial identity in
full. It keeps no attempt bookkeeping; a failure is a non-zero exit and a
log line, and it is the one mode that prints its own progress. Results go to
their own table, read by their own function, so nothing filters the baseline
out of the participant aggregate.

## After a re-pin

The digest a node submits is the arena pin of its installed package; the map
that must contain it is the hub's `ARENA_MAJOR_BY_DIGEST` in
`src/nethackers/arena_version.py`. Deploy the hub before upgrading the node,
or every submission from the node answers 400 and nothing lands.
Classifying is a step in [releasing.md](releasing.md): the current major
when the rebuild cannot move a score, a new major when it can. A bump
touches no rows; the previous major's atoms stop being read, every program
reports zero coverage, and the corpus is re-earned by running the whole grid
again, one program at a time. Run `--baseline` again too: the baseline is
scoped by the same major, and the delta column is empty until it is.
