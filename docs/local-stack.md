# The local stack

One `make` per worktree brings up your own hub and arena, and the mutator
arrives with the first `evolve`, so the whole publish → register → board
loop runs against a throwaway repository and database instead of production.
You need Docker, uv and curl, and several GB of disk for the images; on
Apple Silicon the images run under emulation ([setup.md](setup.md)).
Everything else about developing is in [contributing.md](contributing.md).

## Browse the hub

```bash
make stack          # once per worktree: allocates a port and a compose project name
make hub            # stub auth + fixtures, offline; seconds once the hub image is built
nethackers whoami   # prints the stage, the hub URL, and who you are
```

The hub is at `http://localhost:<port>`, the port drawn from 28000 to 28999
by hashing the worktree path. `<slug>` is the worktree's directory name,
lowercased, with runs of other characters turned into `-`. You are
`offline`, the fixtures fill the boards, and the TUI's boards, frontier and
elites now read this hub. If you have run `nethackers login`, `whoami` warns
that this hub will not accept that login. Every command run inside the
worktree prints `stage: <slug> · hub <url>` on stderr. To see production from
here, `nethackers --prod`.

Without a prior `make stack`, `make hub` starts a directory-named project on
port 8000 while the CLI still points at production. Run `make stack` first.

## Run the loop

```bash
make up HUB_AUTH=github        # the hub with real GitHub auth, then this worktree's arena image
nethackers login               # once per machine
nethackers evolve val-dwa-law-fem --seed autoascend --operator claude --iterations 1
nethackers leaderboard --scope val-dwa-law-fem            # this worktree's own board
nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check    # an exact published commit, back
```

`make up` starts the hub first and only then builds the arena image, so a
slow or failing build never blocks browsing. Run `make stack` before the
first `make up`; without it the first run builds the shared
`nethackers/arena:dev` tag instead of this worktree's own. An `arena:<slug>`
built before the amd64 rule trips the platform check at `evolve` start;
rebuild it with `make arena`. The mutator needs no step: `evolve` pulls the
image CI published for this worktree's files, or builds it. Publishing goes
to a throwaway public `github.com/<login>/nh-dev-<slug>` repository, one ref
per run (`evo-harness-v1/<run-id>`). Nothing is stubbed in the mutator: the
agent, the sandbox and the eval are real, because the bugs this path exists
to catch live in that interaction. The mutator reaches your model
credential through the host-side broker, as in production; on Linux with
`ufw`, add the rule `nethackers setup` prints first
([setup.md](setup.md)), or the run stops with it.

## Modes

| | identity | data | GitHub calls | can register |
|---|---|---|---|---|
| `make hub` (stub) | `offline`, from a fixed token | fixtures, plus a fake private tier so the Private board renders | none | no |
| `make hub HUB_AUTH=github` | your GitHub login | whatever the `hubdata` volume holds; empty only on a fresh one | real: your login, the commit's existence; ownership is your login against the repo owner | with the pinned arena, below |
| `nethackers --prod` | your GitHub login | production | real | yes |

A stored login against the stub hub still publishes to a real public
`nh-dev-<slug>` on GitHub, then records `local-only: auth failed …`. The
github overlay sets no private tier, so a github-auth stack has no Private
board.

## Registering needs the pinned arena

`.env.stack` points `NETHACKERS_ARENA_IMAGE` at this worktree's own
`arena:<slug>` build, so the loop runs against it, and every registration is
refused as `local-only`: a tag names movable bytes, so it is unclassified,
and the local hub runs the same admission code as production
([verification.md](verification.md#what-resets-the-private-board)). The run
log shows only the 400; the hub's reason is `UnclassifiedArena`. To exercise
register → board, run the pinned image instead; the process environment
beats `.env.stack`:

```bash
export NETHACKERS_ARENA_IMAGE=$(nethackers --version -o json | jq -r .images.arena)
nethackers evolve val-dwa-law-fem --seed autoascend --operator claude --iterations 1
```

`--image <pin>` on `eval`, `evolve` or `submit` does the same for one
command. Keep the worktree tag when you are changing arena code; that is the
one thing the pin cannot do.

## Check it worked

After one iteration against the github-auth hub with the pinned arena:

| Check | Command | Expect |
|---|---|---|
| the repo exists and is public | `gh repo view <login>/nh-dev-<slug> --json visibility` | `{"visibility":"PUBLIC"}` |
| the commit is on its run ref | `git ls-remote https://github.com/<login>/nh-dev-<slug> 'refs/heads/evo-harness-v1/*'` | the run's sha |
| the registration was accepted | `<worktree>/.nethackers/runs/<id>/metrics.jsonl` (`runs/latest` points at the newest) | `"outcome": "registered"`, never `"local-only"` |
| the board updated | `nethackers leaderboard --scope val-dwa-law-fem` | the program, with its self-reported score |
| pull fetches the exact commit | `nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check && git -C /tmp/check rev-parse HEAD` | `<sha>` |

This is a manual procedure; there is no automated test for it yet.

## Stop, reset, clean up

```bash
make down                                       # stops the hub; the image and the volume stay
make hub-reset                                  # drops the database volume and restarts the stub hub
gh repo delete <login>/nh-dev-<slug> --yes      # the throwaway repository
git worktree remove <path>                      # when the worktree itself goes
```

`make hub-reset` always brings back the stub hub. For a fresh github-auth
database: `make hub-down`, `docker volume rm nethackers-<slug>_hubdata`,
`make hub HUB_AUTH=github`. Use the make targets, not a bare
`docker compose down -v`: each worktree's stack runs under its own project
name, and a plain `docker compose` in your shell resolves to a
directory-named project and leaves the real volume untouched.

## Files and knobs

| File | Role |
|---|---|
| `compose.yaml` | the hub service, its port, the `hubdata` volume, the DB path |
| `compose.override.yaml` | stub auth over one offline identity, fixtures, and a fake private tier; merged automatically when no `-f` is given |
| `compose.github.yaml` | the real GitHub App auth provider and no fixtures; used with an explicit `-f compose.yaml -f compose.github.yaml` |
| `.env.stack` | gitignored, written by `make stack`: stage name, hub URL and port, compose project, data root, repo name, arena tag |

How compose merges files, names projects and reads env files is Docker's
documentation:
[merge](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/),
[project name](https://docs.docker.com/compose/how-tos/project-name/),
[environment variables](https://docs.docker.com/compose/how-tos/environment-variables/envvars/).

## Self-hosting

The github-auth hub is a private hub instance. Put `compose.yaml` and
`compose.github.yaml` on a host you control, point `NETHACKERS_CLIENT_ID` at
your own GitHub App, and set `NETHACKERS_HUB=https://your-host` in every
client's environment; a Private board needs `NETHACKERS_HIDDEN_SECRET`,
`NETHACKERS_HIDDEN_SEEDS` and `NETHACKERS_VERIFIER_TOKENS` on the hub too.
We don't support it: you host it, you own it.
