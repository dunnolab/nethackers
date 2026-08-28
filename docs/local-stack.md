# The local full-loop stack

One command per worktree brings up an isolated hub + arena + mutator
stack, so `nethackers evolve` can run the whole register -> board loop
against a throwaway repo/DB instead of production. This is the practical,
runnable form of the stage-config-model design
(`docs/superpowers/specs/2026-08-27-stage-config-model-design.md`) --
read that doc for the *why*, this one for the *how*.

## Quickstart: the full loop, one worktree

`make up`'s prerequisite chain (`stack arena hub wait-hub`) allocates
`.env.stack` and builds the arena image *inside the same `make`
invocation*. But Make's `-include .env.stack` only reads that file as it
existed **before** parsing started -- so on a brand-new worktree, the very
first `make up` still resolves `ARENA_IMAGE` to the shared
`nethackers/arena:dev` fallback, not this worktree's own tag (it
self-heals on the second invocation, once the file already exists at
parse time). Run `make stack` by itself first, as its own step, so the
tag is already allocated by the time `make up` parses:

```console
cd <worktree>
make stack                              # allocate .env.stack first (own port/project/tags)
make up HUB_AUTH=github                 # build arena:<slug> · hub on the stage port, real auth, empty DB
make mutator                            # once per machine -- the operator sandbox image
nethackers login                        # once per machine -- GitHub device flow
nethackers evolve val-dwa-law-fem --seed roots/autoascend --operator claude --iterations 2
nethackers leaderboard --objective val-dwa-law-fem       # this worktree's own board
nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check   # fetch an exact published commit back
```

Every value above -- hub port, compose project, data root, repo name,
image tags -- comes from the stage, not a flag. `nethackers whoami` is
the fastest sanity check that discovery is wired up: run it anywhere
inside the worktree and it reports the active stage name and hub URL.

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
3. **Registration accepted.**
   The run log shows `registered`, never `local-only` or a
   publish/register failure; `nethackers search --owner <login>` lists
   the solution -- meaning the full ladder passed with real auth: login
   resolved, repo ownership OK, commit existence OK against
   api.github.com.
4. **Board updated.**
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
