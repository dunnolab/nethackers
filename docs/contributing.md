# Contributing

This repository is the CLI, the hub, the arena and these docs. Bots and
harnesses are the point of the project, and they arrive as `repo@commit`
registrations, never as pull requests: improve a bot with
[`nethackers submit`](../README.md#use), build your own search with
[harness.md](harness.md). The rest of this page is for changes to the code.

## Please do, please don't

- Fixes, docs, doctor and setup recipes, TUI polish: open the PR.
- New commands, hub API changes, anything that touches scoring, the arena or
  the sandbox images: open an issue first; these have a design behind them.
- Coding agents are welcome. You must understand and be able to defend every
  line, and the PR description is in your own words.
- Don't send a bot as a PR. Register it.

## Setup

```bash
git clone https://github.com/dunnolab/nethackers
cd nethackers
uv run nethackers --help          # resolves the environment on first run
```

Python 3.11+ and [uv](https://docs.astral.sh/uv/); there is no install step.
`uv run nethackers` always runs live source. `uv tool install --from .` can
serve a stale wheel for non-Python assets such as the web page; `make
install` clears the cache first.

## Tests

```bash
make test     # the fast suite: no NLE, no Docker, no live agent CLIs
make check    # mypy + ruff
make smoke    # the `docker` suite; needs the arena and mutator images
```

`make test` skips the `nle`, `docker`, `codex_live` and `claude_live`
markers; CI runs the same suite plus mypy, ruff and a compose smoke job.
Style is ruff (`E,F,I,UP,B,SIM`, line length 100) and mypy over `src` and
`tests`.

One trap: many functions take dependencies as keyword defaults
(`run=subprocess.run`, `repo_root=...`). Those bind at import time, so
`monkeypatch.setattr` on the module attribute is a silent no-op and the test
passes while testing nothing. Inject explicitly.

## Against a real hub

Unit tests are not enough for anything touching boards, registration or the
web page.

```bash
make stack                  # allocate this worktree's port and compose project, once
make hub                    # stub auth + fixtures, offline, seconds to start
make hub HUB_AUTH=github    # real GitHub auth against an empty DB
make up                     # hub + the arena image, for `evolve`
make hub-reset              # wipe the DB and start over
```

Each worktree gets its own port, compose project and arena tag, so parallel
checkouts don't collide. The whole loop, and how to register against a local
hub: [local-stack.md](local-stack.md). To reach production from inside a
worktree: `nethackers --prod`.

## Pull requests

- Branch off `main`; PRs target `main`. One change per PR, CI green.
- A change with a design behind it says so in the description: what you
  considered, what you chose, why.
- Docs change in the same PR as the behaviour.
- Two things CI may say. "arena inputs changed since `<tag>` without
  re-pinning `ARENA_IMAGE`" means something under `arena/`, `nle-base/`,
  `uv.lock`, `src/nethackers/arena/` or `src/nethackers/contracts/` moved
  since the last release tag; the fix is the re-pin step in
  [releasing.md](releasing.md), and the diff base is the last `v*` tag, not
  your branch point, so it can fire for someone else's unreleased merge. And
  a PR that touches `Dockerfile.mutator`, its entrypoint or the code the
  mutator copies gets its image rebuilt and re-pinned by a bot commit on the
  branch; wait for that commit before merging.

## Reporting a problem

[troubleshooting.md](troubleshooting.md) first. For an issue, include:

```bash
nethackers --version -o json     # version, run schema, pinned image digests
nethackers doctor -o json        # the environment
nethackers report                # the last crash, if there was one
```

`report` is read-only and offline; nothing is ever sent anywhere, and there
is no telemetry in this project.

## Releasing

Maintainers only: [releasing.md](releasing.md).
