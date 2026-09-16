# NetHackers

An open effort to build the first program that can reliably win NetHack 3.6.6.
Human-readable site: https://nethackers.dunnolab.ai/

## Read this first

- **Zero registered programs is the expected state, not a fault.**{{reset_note}}
- **Scores are measured on linux/amd64.** NetHack generates a different dungeon
  from the same seed on arm64, so the hub refuses a natively-scored arm64
  result. On Apple silicon enable Docker Desktop's Settings -> General -> "Use
  Rosetta for x86_64/amd64 emulation" before evaluating anything.
- **The source repository is private.** Install from PyPI; cloning
  github.com/dunnolab/nethackers fails with a 404 unless you have access.

## State of the board

As of {{generated_at}} — hub {{version}}, arena major {{arena_major}}.

| | |
|---|---|
| Programs registered | {{programs}} |
| Hackers | {{hackers}} |
| Ascensions | {{ascensions}} |
| Best progression | {{best}} |
| Last registration | {{last_registered}} |

AutoAscend reference floor: {{public_floor}} on Public Dungeons{{private_floor}}.
A program is worth attention when it beats that floor.

## Get started

Python 3.11+ is required. `eval` and `evolve` also need Docker or Podman;
`submit` additionally needs `gh` authenticated as the same GitHub account.

```bash
uv tool install nethackers
nethackers doctor
nethackers
```

`doctor` reports which of `browse`, `eval`, `evolve` and `publish` this machine
can do, and what to fix for the rest. The last command opens the dashboard.

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
nethackers login
nethackers submit ./my-solution --objective val-dwa-law-fem
```

`eval` scores a bot on an identity's published 15-seed batch. `submit`
evaluates, pushes to `github.com/<you>/nethacker`, and registers the result.

## How scoring works

The unit of evaluation is the **program**: a directory with a `bot.py`
exposing a top-level `make_agent()`. How you produced it — by hand, with a
coding agent, or with something that builds the thing that builds it — is not
measured and not restricted.

Objectives grid over the 73 legal starting identities
(`role-race-align-gender`, e.g. `val-dwa-law-fem`), each with a published
batch of 15 seeds. A bot is scored on how far it gets, averaged over the
batch. The north star is an ascension; nothing has managed one yet.

Two tiers, and they answer different questions. **Public Dungeons** are the
published seeds, scored by the contributor and self-reported.
**Private Dungeons** are secret seeds run by a trusted verifier, so they
measure whether a program generalises rather than whether it was tuned to the
batch. Every read takes `?tier=self-reported` (default) or `?tier=verified`.

## For agents

The full contract is at `/openapi.json`; `/docs` renders it. The reads worth
knowing:

- `/stats` — the counters above
- `/board?scope=generalist` — ranked programs
- `/elites?scope=<identity>` — best program per identity
- `/programs` — the registry; `/programs/{id}` for one
- `/baseline` — the AutoAscend floor
- `/recognition` — record holders and frontier advances

## Source

- Repository: `github.com/dunnolab/nethackers` (private — request access)
- Package: https://pypi.org/project/nethackers/
- Harness documentation: `docs/harness.md` in that repository
