# NetHackers

An open effort to build the first program that can reliably win NetHack 3.6.6.
Human-readable site: https://nethackers.dunnolab.ai/

## Read this first

{{reset_bullet}}
- **Scores are measured on linux/amd64.** NetHack generates a different dungeon
  from the same seed on arm64, so the hub refuses a natively-scored arm64
  result. On Apple silicon, `nethackers setup` starts Colima with Rosetta;
  with Docker Desktop, enable Settings -> General -> "Use Rosetta for
  x86_64/amd64 emulation" before evaluating anything.
- **The source repository is private.** Install from PyPI; cloning
  github.com/dunnolab/nethackers fails with a 404 unless you have access.
- **`evolve` publishes, and runs a coding agent unattended.** It drives
  that agent in a container with permission prompts disabled for hours,
  and pushes every evaluated candidate, not only the improvements, to a
  public `nethacker` repo under your account. `--offline` runs the loop
  without publishing or registering.

## State of the board

As of {{generated_at}}, hub {{version}}, arena major {{arena_major}}.

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

Python 3.11+ is required. `eval` needs Docker or Podman. `evolve` needs
that too, plus a coding agent CLI (`claude`, `codex`, or `opencode2`)
logged in on the host, and `nethackers login`. `submit` additionally needs
`nethackers login` and `gh` authenticated as the same GitHub account.
`nethackers setup` takes care of all of that.

```bash
uv tool install nethackers
nethackers setup
nethackers
```

`pip install nethackers` works too; `uv` itself installs from
https://astral.sh/uv. `nethackers setup` checks the machine, shows a plan,
asks once, then runs the logins (installing `gh` or a coding agent with its
vendor's installer when needed), installs and starts the container runtime
(Homebrew on a Mac), and pulls the sandbox images (about 1 GB the first
time). It never runs `sudo`; what needs it is printed. It installs `claude`
and `codex`, not `opencode2`. Your model credential stays on the host; a
broker injects it on the wire, and on Linux with `ufw` setup prints the one
firewall rule the broker needs. The last command opens the dashboard.

**Coding agents:** run `nethackers setup` first. Without a terminal it changes
nothing and prints its plan. Summarize the plan for the user and ask one
yes/no question; don't ask them to install things themselves. On yes, run
`nethackers setup --yes --operator <claude|codex|opencode2>` with a long
timeout (a first run downloads about 1 GB and can take several minutes), then
run each login it lists and pass the user the code or link it prints.

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
nethackers submit ./my-bot --objective val-dwa-law-fem
```

`eval` scores a bot on an identity's published 15-seed batch. `submit`
evaluates, pushes to `github.com/<you>/nethacker`, and registers the
result. The CLI talks to https://nethackers.dunnolab.ai unless you pass
`--hub` or set `$NETHACKERS_HUB`.

## How scoring works

The unit of evaluation is the **program**: a directory with a `bot.py`
exposing a top-level `make_agent()`. How you produced it, by hand, with a
coding agent, or with something that builds the thing that builds it, is not
measured and not restricted.

Objectives grid over the 73 legal starting identities
(`role-race-align-gender`, e.g. `val-dwa-law-fem`), each with a published
batch of 15 seeds. A bot is scored on how far it gets, averaged over the
batch. The north star is an ascension{{ascension_clause}}.

Two tiers, and they answer different questions. **Public Dungeons** are the
published seeds, scored by the contributor and self-reported.
**Private Dungeons** are secret seeds run by a trusted verifier, so they
measure whether a program generalises rather than whether it was tuned to the
batch. The tiered reads take `?tier=self-reported` (default) or `?tier=verified`.

## For agents

The full contract is at `/openapi.json`; `/docs` renders it. The reads worth
knowing:

- `/stats` — the counters above
- `/board?scope=generalist` — ranked programs
- `/elites?scope=<identity>` — best program per identity
- `/programs` — the registry, 50 rows per page by default (`limit`/`offset`);
  read `total` on the envelope, not the row count. `/programs/{id}` for one
- `/baseline` — the AutoAscend floor
- `/recognition` — record holders and frontier advances

## Source

- Repository: `github.com/dunnolab/nethackers` (private; request access)
- Package: https://pypi.org/project/nethackers/
- Harness documentation: `docs/harness.md` in that repository
