# The local stack

One `make` per worktree brings up your own hub, arena and mutator, so
`nethackers evolve` runs the whole publish → register → board loop against a
throwaway repository and database instead of production. You need Docker,
uv, and about 5 GB of disk; on Apple Silicon the images run under emulation
([setup.md](setup.md)). Everything else about developing is in
[contributing.md](contributing.md).

## Browse the hub

```bash
make stack          # once per worktree: allocates a port and a compose project name
make hub            # stub auth + fixtures, offline; up in seconds
nethackers whoami   # prints the stage, the hub URL, and who you are
```

The hub is at `http://localhost:<port>`, the port drawn from 28000 to 28999
by hashing the worktree path. You are `offline`, the fixtures fill the
boards, and the TUI's boards, frontier and elites now read this hub. To see
production from here, `nethackers --prod`.

## Run the loop

```bash
make up HUB_AUTH=github        # the hub with real GitHub auth and an empty DB, then this worktree's arena image
nethackers login               # once per machine
nethackers evolve val-dwa-law-fem --seed autoascend --operator claude --iterations 1
nethackers leaderboard --objective val-dwa-law-fem        # this worktree's own board
nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check    # an exact published commit, back
```

`make up` starts the hub first and only then builds the arena image, so a
slow or failing build never blocks browsing. Run `make stack` before the
first `make up`; without it the first run builds the shared
`nethackers/arena:dev` tag instead of this worktree's own. The mutator needs
no step: `evolve` pulls the image CI published for this worktree's files, or
builds it. Publishing goes to a throwaway `github.com/<login>/nh-dev-<slug>`
repository, one ref per run (`evo-harness-v1/<run-id>`). Nothing is stubbed
in the mutator: the agent, the sandbox and the eval are real, because the
bugs this path exists to catch live in that interaction.

## Modes

| | identity | data | GitHub calls | can register |
|---|---|---|---|---|
| `make hub` (stub) | `offline`, from a fixed token | fixtures | none | no (stub token) |
| `make hub HUB_AUTH=github` | your GitHub login | empty | real: login, repo ownership, commit existence | with the pinned arena, below |
| `nethackers --prod` | your GitHub login | production | real | yes |

## Registering needs the pinned arena

`.env.stack` points `NETHACKERS_ARENA_IMAGE` at this worktree's own
`arena:<slug>` build, so the loop runs against it, and every registration is
refused as `local-only`: a tag names movable bytes, so it is unclassified,
and the local hub runs the same admission code as production
([verification.md](verification.md#what-resets-the-private-board)). To
exercise register → board, run the pinned image instead; the process
environment beats `.env.stack`:

```bash
export NETHACKERS_ARENA_IMAGE=$(python -c 'from nethackers._image_pins import ARENA_IMAGE; print(ARENA_IMAGE)')
nethackers evolve val-dwa-law-fem --seed autoascend --operator claude --iterations 1
```

`--image <pin>` on `eval`, `evolve` or `submit` does the same for one
command. Keep the worktree tag when you are changing arena code; that is the
one thing the pin cannot do.

## Check it worked

After one iteration against the github-auth hub with the pinned arena:

| Check | Command | Expect |
|---|---|---|
| the repo exists and is public | `gh repo view <login>/nh-dev-<slug> --json visibility` | `PUBLIC` |
| the commit is on its run ref | `git ls-remote https://github.com/<login>/nh-dev-<slug> 'refs/heads/evo-harness-v1/*'` | the run's sha |
| the registration was accepted | the run log under `~/.nethackers/evolve/runs/<id>/` | `registered`, never `local-only` |
| the board updated | `nethackers leaderboard --objective val-dwa-law-fem` | the program, with its self-reported score |
| pull fetches the exact commit | `nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check && git -C /tmp/check rev-parse HEAD` | `<sha>` |

This is a manual procedure; there is no automated test for it yet.

## Stop, reset, clean up

```bash
make down                                       # stops the hub; the arena image stays
make hub-reset                                  # drops the database volume and restarts
gh repo delete <login>/nh-dev-<slug> --yes      # the throwaway repository
```

Use the make targets, not a bare `docker compose down -v`: each worktree's
stack runs under its own project name, and a plain `docker compose` in your
shell resolves to a directory-named project and leaves the real volume
untouched.

## Files and knobs

| File | Role |
|---|---|
| `compose.yaml` | the hub service, its port, the `hubdata` volume, the DB path |
| `compose.override.yaml` | stub auth over one offline identity plus fixtures; merged automatically when no `-f` is given |
| `compose.github.yaml` | the real GitHub App auth provider and an empty DB; used with an explicit `-f compose.yaml -f compose.github.yaml` |
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
client's environment. We don't support it: you host it, you own it.
