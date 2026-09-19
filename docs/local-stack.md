# The local full-loop stack

One command per worktree brings up an isolated hub + arena + mutator
stack, so `nethackers evolve` can run the whole register -> board loop
against a throwaway repo/DB instead of production. This is the practical,
runnable form of the stage-config model.

## Quickstart

### Just browse the hub / drive the TUI

You don't need the arena eval image to look at boards, so skip `make up`
(which builds arena) and start the hub on its own -- it comes up in
seconds:

```console
cd <worktree>
make hub                        # stub auth + fixtures (offline), on this worktree's port
# or:  make hub HUB_AUTH=github  # real GitHub auth, empty DB
nethackers whoami               # sanity check: reports the active stage + hub URL
```

The TUI's boards/frontier/elites then connect to *this worktree's* hub. To
browse the global hub from inside a worktree instead, `nethackers --prod`.

### The full evolve loop

Running evolutions additionally needs the arena eval image. `make up`
brings the hub up **first** -- so a slow or failing arena build can never
gate it -- and only then builds this worktree's `arena:<slug>`. Run `make
stack` by itself first so the per-worktree arena tag is already allocated
when `make up` parses its `-include .env.stack` (otherwise the very first
`make up` builds the shared `nethackers/arena:dev` fallback instead; it
self-heals on the second run):

```console
cd <worktree>
make stack                              # allocate .env.stack first (own port/project/tags)
make up HUB_AUTH=github                 # hub up first (real auth, empty DB), THEN builds arena:<slug>
nethackers login                        # once per machine -- GitHub device flow
nethackers evolve val-dwa-law-fem --seed roots/autoascend --operator claude --iterations 2
nethackers leaderboard --objective val-dwa-law-fem       # this worktree's own board
nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check   # fetch an exact published commit back
```

There is no mutator step: `nethackers evolve` fetches the operator sandbox
image that matches this worktree's files, pulling it when CI has published it
and building it otherwise.

Both sandbox images here are `linux/amd64`, emulated on Apple Silicon: `make up`
builds `arena:<slug>` (and the `nle-base` under it) for that platform, so the
first build is slower there, and the mutator is built or pulled for it too. The
coding agent scores its own candidates inside the mutator, so `evolve` refuses
to start when it finds the arena and mutator built for different platforms,
since the agent would then tune games the arena never plays. An `arena:<slug>`
built natively before this rule existed trips that check; the refusal names the
`make arena ARENA_IMAGE=...` that rebuilds it.

Every value above -- hub port, compose project, data root, repo name,
image tags -- comes from the stage, not a flag. `nethackers whoami` is
the fastest sanity check that discovery is wired up: run it anywhere
inside the worktree and it reports the active stage name and hub URL.

### The per-worktree arena tag cannot register

`.env.stack` sets `NETHACKERS_ARENA_IMAGE=nethackers/arena:<slug>`, so the
loop above **runs** against this worktree's own arena build -- and every
registration it attempts is refused, recorded as `local-only` in the run
log.

That is by design, not a bug to work around. Since the amd64 reference
reset the hub admits evidence only from an arena digest classified at the
current `ARENA_MAJOR`. A tag names movable bytes, so `arena:<slug>` is
unclassified *by construction* and `register` raises `UnclassifiedArena`.
The local hub runs the same admission code as prod, so it refuses it too.

To exercise register -> board locally, run the **pinned** arena instead --
process env beats `.env.stack`, so one variable is enough:

```console
export NETHACKERS_ARENA_IMAGE=$(python -c \
  'from nethackers._image_pins import ARENA_IMAGE; print(ARENA_IMAGE)')
nethackers evolve val-dwa-law-fem --seed roots/autoascend --operator claude --iterations 1
```

(`--image <pin>` on `eval`/`evolve`/`submit` does the same for one command.)
On Apple Silicon that runs under emulation -- see `nethackers doctor`'s
Rosetta advisory. Conversely, keep the worktree tag when you are actually
changing arena code: that is the one thing the pin cannot do.

The mutator is deliberately **not stubbed**. The bugs this path exists to
catch live in the real agent x sandbox x eval interaction (codex's
login-shell PATH loss, operator auth injection, stale-image drift, the
401-token-refresh registration bug) -- none of which a fake operator
reproduces. The harness registers every scored program regardless of
whether it improved on the parent (`src/nethackers/harness/loop.py`), so
one completed iteration always yields at least one publish +
registration -- the plumbing is deterministic even though the operator's
output isn't. Publishing goes to the throwaway `nh-dev-<slug>` repo, on
the per-run ref `evo-harness-v1/<run-id>`.

## Real-auth local hub mode

`nethackers register`/the board only mean something if the hub actually
validated the caller against GitHub -- login, repo ownership, commit
existence. A stub hub can't exercise that ladder at all (there's no real
identity behind `dev-token`), so register -> board fidelity needs the
local hub to run the **real** GitHub App auth provider, with everything
real except the database. The hub server itself needs no code change for
this -- `create_default_app` (`src/nethackers/hub/api.py`) already reads
its auth choice purely from the environment; compose is the adapter that
decides which env is set.

Three compose files, the canonical base/override/overlay split:

| File | Role | Auth |
|---|---|---|
| `compose.yaml` | base -- service, port, `hubdata` volume, the DB path | none (no auth env at all) |
| `compose.override.yaml` | checked in; compose auto-merges it whenever a command runs with **no** explicit `-f` | offline stub auth over a single dev identity + the fixture-seeded demo catalog -- today's behavior, unchanged |
| `compose.github.yaml` | used only with an **explicit** `-f compose.yaml -f compose.github.yaml`, which disables the auto-override merge | the real GitHub App auth provider (client id defaulted to the same public App id as `Stage().github_client_id`); no fixtures -- an empty DB, same as prod |

The `Makefile`'s `HUB_AUTH` switch (default `stub`) is the whole flip:

```console
make up HUB_AUTH=github     # real auth, empty DB
make up                     # (or HUB_AUTH=stub) -- offline stub auth + fixtures, unchanged
```

Bare `docker compose up` (no `-f`, no `HUB_AUTH`) still behaves exactly
as it did before this split -- `tests/test_compose_smoke.py` exercises
exactly that path and stays green.

Division of labor, stated plainly: **stub mode** is for everything
offline -- board/UI/fixture/migration work, fast, no network, fake
identities. **Real-auth mode** is for anything that touches
register -> board: the hub resolves the caller's real GitHub login,
repo ownership passes because the account actually owns `nh-dev-<slug>`,
and the commit-existence check finds the actually-pushed public commit.
The only difference from prod is the DB pointing at a local sqlite
volume instead of the production one.

Auth is not the only gate on that path any more: the hub also checks the
*arena* the evidence came from, in both modes. Real-auth mode plus the
per-worktree arena tag gets you through the GitHub ladder and then stops
at admission, so pair it with the pinned image (above) whenever the thing
under test is register -> board rather than the board itself.

To self-host against your own GitHub App instead of the shared default,
override the client id: `NETHACKERS_CLIENT_ID=<your app id> make up HUB_AUTH=github`.

## Validation: full-loop invariants

A real operator is non-deterministic, so this validates **plumbing
invariants, not scores**. After one `nethackers evolve ... --iterations 1`
against the real-auth stack above:

1. **Repo auto-created, public.**
   `gh repo view <login>/nh-dev-<slug> --json visibility` reports
   `PUBLIC`. (`ensure_repo`, `src/nethackers/hubclient/publish.py`, probes
   with `gh repo view` and creates the repo public when absent -- a path
   your own `nethacker` repo, which already exists, never exercises. A
   private repo would 404 the hub's commit check and silently drop every
   registration -- exactly the failure this step guards against.)
2. **Commit on the per-run ref.**
   `git ls-remote https://github.com/<login>/nh-dev-<slug> 'refs/heads/evo-harness-v1/*'`
   lists the run's sha -- the parallel-safe one-ref-per-run contract.
3. **Registration accepted.** *(requires the pinned arena image -- see
   "The per-worktree arena tag cannot register" above; with the default
   `arena:<slug>` this criterion reads `local-only` and cannot pass.)*
   The run log shows `registered`, never `local-only` or a
   publish/register failure; `nethackers search --owner <login>` lists
   the solution -- meaning the full ladder passed with real auth: login
   resolved, repo ownership OK, commit existence OK against
   api.github.com, and the evidence came from a classified arena digest.
4. **Board updated.** *(same precondition as 3.)*
   `nethackers leaderboard --objective val-dwa-law-fem` shows the
   program with its self-reported dev score.
5. **Pull fetches the exact commit.**
   `nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check` succeeds, and
   the clone's `HEAD` equals `<sha>` -- fetch-by-SHA on a non-default
   ref.

Clean up the throwaway repo when done:
`gh repo delete <login>/nh-dev-<slug> --yes`.

This checklist is a **manual procedure**, run by hand against a real
GitHub account -- there is no automated test for it yet. A `github_live`
pytest marker, excluded from the fast gate the same way `claude_live`/
`codex_live` already are, would be the natural way to automate it later;
that marker is reserved in `pyproject.toml` but nothing uses it yet.

## Self-hosting (a documented byproduct)

The real-auth local hub described above **is** a private hub instance --
there is no separate "self-hosting" feature to build. Put `compose.yaml`
+ `compose.github.yaml` on any host you control, point
`NETHACKERS_CLIENT_ID` at your own GitHub App, and set
`NETHACKERS_HUB=https://your-host` in every client's environment; that is
the entire recipe. The per-worktree stage above is just the ephemeral,
laptop-scale case of the same mechanism -- a future "staging" stage (a
hand-written env file on a persistent host, per the stage-config design's
three-stages table) is the identical file, just not thrown away at the
end of a session.
