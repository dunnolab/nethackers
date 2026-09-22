```
 _  _     _   _  _         _
| \| |___| |_| || |__ _ __| |_____ _ _ ___
| .` / -_)  _| __ / _` / _| / / -_) '_(_-<
|_|\_\___|\__|_||_\__,_\__|_\_\___|_| /__/
```

**An open effort to build the first program that can reliably win NetHack 3.6.6.**

[nethackers.dunnolab.ai](https://nethackers.dunnolab.ai) · [PyPI](https://pypi.org/project/nethackers/) · Apache-2.0

NetHack (1987) is among the oldest unsolved challenges in games. NLE put it
forward as a grand challenge for AI; it is still unsolved, and it is the
hardest game in the BALROG suite. NetHackers is an attempt to find out —
together — whether a coding agent can write the program that cracks it.

What gets scored is the **program**: a symbolic bot, cheap to run and — as long
as you keep it deterministic — replayable. How you produce that program is
entirely your business. Write it by hand, evolve it with a coding agent, or build
a better thing that builds it — the hub never runs your search and never assigns
you work.

---

## Contents

- [How it works](#how-it-works)
- [Public and Private Dungeons](#public-and-private-dungeons)
- [Safety: this runs untrusted code](#safety-this-runs-untrusted-code)
- [Install](#install)
- [Quickstart](#quickstart)
- [GitHub is the infrastructure](#github-is-the-infrastructure)
- [Documentation](#documentation)

---

## How it works

The unit of evaluation is one bot directory containing a `bot.py` with a
top-level `make_agent()`. That's the whole contract — see
[`docs/harness.md`](docs/harness.md).

Objectives grid over the **73 legal starting identities** (`role-race-align-gender`,
e.g. `val-dwa-law-fem`): NetHack 3.6.6's 38 valid (role, race, align) triples, each
crossed with both genders — except Valkyrie's 3, which the game locks to female.
35×2 + 3 = 73. Each identity has a published batch of **15 seeds**. A bot is
scored on how far it gets, averaged over that batch; the north star is an
**ascension**, which nothing has managed yet.

The hub keeps the best program per identity (the **elites**). Anyone can pull an
elite, improve it, and register the result — so one contributor's improvement
becomes everyone's parent.

```
   hub ──pull elite──▶ your machine ──mutate──▶ candidate
    ▲                                              │
    │                                          evaluate
    └──────────register (repo@commit)◀─────── (sandboxed arena)
```

Our own evolutionary harness — a coding agent in a container, MAP-Elites over
the 73 identities — ships in this repo. It is the worked example, not the
required path. [`docs/harness.md`](docs/harness.md) documents it as something to
read and reuse when building your own.

## Public and Private Dungeons

Every program carries up to two scores, and they answer different questions.

| | **Public Dungeons** | **Private Dungeons** |
|---|---|---|
| Tier in the API/DB | `self-reported` | `verified` |
| Seeds | 15 published per identity | secret, set per deployment (15 in production) |
| Who ran it | you, on your machine | our verifier, on our hardware |
| Reproducible by you | yes, given a deterministic bot | no — you never see the seeds |
| What it measures | how good this bot is on seeds it could tune against | whether that generalizes |

**Public** is where you work. The seeds come from a published secret, so
`nethackers eval` on your laptop produces the same batch as everyone else's and
you can iterate against it freely. This is the intended target, not a consolation
prize: NetHack is unsolved by a wide margin, and the starting bet is that plainly
getting better at the public dungeons is where the first generalizable gains come
from.

**Private** answers the other question — does it transfer? A trusted verifier
re-runs registered programs on seeds held secret by the hub and submits an
independent score, so nobody has to take a self-reported number on faith. The
website defaults to Private because it is the harder question; it is not a verdict
on how you got there.

The two words are deliberately kept apart. *Verified* is the trust level (a
trusted worker produced this number). *Hidden* is the seeds it happened to use.
Full mechanism, including the verifier's scheduler:
[`docs/verification.md`](docs/verification.md).

### Reference architecture

`linux/amd64` is the scoring architecture — every score the hub accepts came
from the pinned amd64 arena image, which is what `nethackers eval` uses by
default. That's what makes "the same batch as everyone else's" (above) true
regardless of what you're running it on.

Other hosts emulate, and that's required, not optional: the same seed plays a
different game of NetHack on a different CPU architecture, so a native arm64
score isn't comparable — the hub refuses it.

On Apple Silicon, turn on Rosetta in Docker Desktop (Settings → General →
Apple Virtualization framework → "Use Rosetta for x86_64/amd64 emulation").
It's worth doing: the same 15-episode batch on the same machine took 823s
under QEMU and 224s with Rosetta. `nethackers doctor` reports whether it's on.

## Safety: this runs untrusted code

> ⚠️ **Read this before running `evolve`, `eval`, or `pull`.**

Three things execute code that neither you nor we wrote or reviewed:

1. **Bots you evaluate.** `nethackers eval` and every eval inside `evolve` import
   and run a `bot.py`. If you pulled it from the hub, someone else wrote it.
2. **The coding agent.** `nethackers evolve` runs Claude Code, Codex, or OpenCode
   with permission prompts fully disabled (`--dangerously-skip-permissions` /
   `--dangerously-bypass-approvals-and-sandbox`). It writes and executes code
   unattended, for hours.
3. **Programs you `pull`.** `nethackers pull` clones a repo. Nothing runs at
   clone time, but you now have a stranger's code on disk that the next `eval`
   will execute.

What we do about it:

- **The bot evaluator is a sealed box.** Every eval runs in a container with no
  network (`--network none`), a read-only root filesystem, a `noexec,nosuid`
  tmpfs for scratch, every Linux capability dropped, `no-new-privileges`, a
  non-root user, and pid/memory/CPU caps. The bot is mounted read-only, and the
  hidden seeds never enter the container: the secret is expanded to concrete
  per-game seeds on the host and piped in over stdin, so it is never on the
  container's argv or in its environment.
- **Fetching is github-only and hardened.** `pull` and hub registration accept
  only `github.com/<owner>/<repo>@<commit>` references. The host is parsed rather
  than string-matched, so lookalikes like `github.com.evil.com` or
  `git@github.com:...` are refused, and the clone allows only https, with
  submodules, symlink checkout, and tags disabled.
- **The coding agent runs in a container** with `no-new-privileges`, pid/memory
  (swap-capped)/CPU limits, a non-root user (it starts as root only to remap uids,
  then drops), and a wall-clock `timeout`. Instruction-bearing files (`CLAUDE.md`,
  `AGENTS.md`, `.mcp.json`, …) are stripped from the tree it is handed, and the
  credential broker (on by default) keeps your model key out of the container
  entirely — injected host-side on the wire, never mounted in.

What we don't do:

- **A bot can still influence its own score.** The scorer runs the bot in-process
  with the solution on its `sys.path`, so a self-reported number is a claim you
  take on trust. Sealing the container does not change that. It is why the Private
  Dungeons (verified) tier exists.
- **Network egress is open by default.** The agent CLIs need their model APIs, so
  the container can reach the network; egress allow-listing is designed but not on
  by default. (Your model *credential* is kept out of the container by the default
  broker — `--no-broker` opts back into mounting it instead, writable for Codex.)
- **The threat model is accident-grade.** It defends against a runaway or confused
  agent and the blast radius of one, not a determined adversary. A container is
  not a boundary against a kernel exploit. If you are evaluating code you have
  reason to distrust, run it on a machine you are willing to lose.

Details, per-surface, are in [`docs/harness.md`](docs/harness.md#3-safety-and-sandboxing),
written to be reused by anyone building a harness of their own.

## Install

**Requirements**

Every row also needs Python 3.11+; the rest are additive per row, not cumulative
down the table.

| To do this | You need |
|---|---|
| Browse the hub (TUI, boards, frontier) | nothing else |
| `eval` — score a bot | Docker or Podman |
| `evolve` — run the loop | Docker or Podman, a coding agent CLI (`claude`, `codex`, or `opencode2`) logged in on the host, and `nethackers login` |
| `submit` — publish a solution | Docker or Podman (it evaluates before pushing), `nethackers login`, and [`gh`](https://cli.github.com/) authenticated as the **same** GitHub account |

OpenCode 2 needs nothing installed on the host: its CLI ships in the sandbox.
Give it models by defining providers in your global
`~/.config/opencode/opencode.json` (or `.jsonc`). Only that file's `provider`
section enters the sandbox, along with the environment variables those
providers reference as `{env:NAME}` or list in their `env` field. So set each
key as `{env:NAME}` or a literal `apiKey`: a `{file:...}` key, a login made with
`opencode2 auth login`, your plugins and MCP servers, and project
`opencode.json` files all stay outside. With no key, runs use OpenCode's free
models, and `doctor` says so. How each coding agent behaves in the sandbox, with
OpenCode 2 in detail, is in [`docs/harness.md`](docs/harness.md#the-coding-agents).

Note that `doctor`'s `publish` capability checks the hub, your login, and `gh` —
but not the container runtime, so it can report `publish` ready on a machine where
`submit` will still stop at its arena evaluation.

**Install the CLI**

```bash
pip install nethackers
# or, isolated:
uv tool install nethackers
```

Don't have `uv`? It installs from https://astral.sh/uv.

**Check your machine**

```bash
nethackers doctor
```

`doctor` runs eight checks and folds them into four capabilities — `browse`,
`eval`, `evolve`, `publish` — telling you exactly which ones this machine can do
and what to fix for the rest. It honors `-o json` if you want to gate a script on
it. It reaches the network (a hub round-trip, and a registry probe for any
sandbox image you don't have locally), and it changes nothing unless you pass
`--pull`.

The sandbox images (`ghcr.io/dunnolab/nethackers-arena` and `-mutator`) are
pinned by digest in the CLI and pulled on first use — several GB, so the first
`eval` takes a while. Nothing else needs building.

## Quickstart

**Browse**

```bash
nethackers          # the dashboard TUI: frontier, boards, elites, your runs
```

Every view is also a plain command, and each prints JSON when piped:

```bash
nethackers frontier                   # how far the community has collectively reached
nethackers board --scope val          # ranked programs; scope = generalist | role |
                                      #   facet (race:elf) | full identity
nethackers elites --scope val-dwa-law-fem   # best program per identity
nethackers search --owner <login>     # registered programs, filtered
nethackers show <prog_id>             # one program in detail
nethackers elites -o json | jq '.[0]'
```

**Score a bot**

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
```

Runs the identity's published 15-seed batch in the sandboxed arena and prints the
result. This is the Public Dungeons number.

**Evolve one**

```bash
nethackers login                                     # GitHub device flow, once
nethackers evolve val-dwa-law-fem \
    --seed autoascend \
    --operator codex \
    --iterations 20
```

The objective is positional; `--seed` points at the starting solution. AutoAscend
ships inside nethackers, so `autoascend` works from anywhere, installed or from a
checkout. The older spelling `roots/autoascend` still resolves to the same tree. Each iteration picks a cell, mutates its elite
with the coding agent in its container, and evaluates the result.

**Every candidate that survives the smoke check and gets evaluated is then pushed
to your public `nethacker` repo and registered** — not only the ones that improve
on their parent. That is deliberate (the archive is meant to record what was
tried, not just what won), but it means a long run publishes a lot of commits
under your account. Use `--offline` to run the loop without publishing or
registering anything.

The objective resolves to a set of identities: a single one (`val-dwa-law-fem`),
a bare role (`val` — all its identities), a comma list, or a glob
(`val-*-law-*`).

**Publish an existing solution**

```bash
nethackers submit ./my-solution --objective val-dwa-law-fem
```

Evaluates it, pushes it to `github.com/<you>/nethacker` (on its `submit`
branch), and registers the resulting `repo@commit` with the hub. Requires `gh`
authenticated as the same account you `nethackers login`'d with.

**Fetch anyone's program**

```bash
nethackers pull github.com/<someone>/nethacker@<commit> ./fetched
```

Anything registered from a public repo is fetchable by anyone, pinned to the
exact commit that was measured.

The hub defaults to `https://nethackers.dunnolab.ai`; override with `--hub` or
`$NETHACKERS_HUB`. `nethackers --help` lists every command.

## GitHub is the infrastructure

We deliberately do not run an artifact store, an identity provider, or a code
host. GitHub is all three, which keeps the hub small enough to be honest about.

- **A program *is* a `repo@commit`.** The hub stores the link, the manifest, and
  the scores — never the code. Registration is rejected unless the reference is a
  real `github.com/<owner>/<repo>` (the host is parsed, so lookalikes are refused)
  and the commit exists (the hub checks it against the GitHub API using *your*
  token), so a board row always pointed at a real tree, on GitHub, when it was made. Two honest limits: the hub does
  not check repo *visibility*, so a private repo can be registered and will not be
  fetchable by others — `submit` forces the repo public, a hand-rolled `register`
  does not — and a link is only as durable as the repo behind it, which its owner
  can force-push, delete, or flip private.
- **Identity is your GitHub account.** `nethackers login` is a GitHub App device
  flow. There is no client secret and no App private key anywhere in this repo or
  on the hub — the hub reads GitHub with the caller's own token. Your board
  identity is your GitHub login.
- **Publishing is a push.** `nethackers submit` uses `gh` to create/push
  `github.com/<you>/nethacker` under your own account. You own your solutions;
  we hold a pointer. Bots go to branches (`submit`, and one per `evolve` run);
  on a newly created repo the default branch holds only a README. While the repo's description is still
  empty, a publish fills it in, links the repo to your page here, tags it
  `nethack`/`nethackers`, and adds a short README if there is none — it never
  overwrites a description, website or README you wrote, and all of it is yours
  to edit or delete.
- **CI is the deploy lever.** Pushing a `vX.Y.Z` tag builds the hub image, pushes
  it to GHCR, and flips production by digest with a health check and automatic
  rollback (skippable with `[skip hub-deploy]` in the tagged commit message). The sandbox images are built by workflow and pinned by digest into
  the CLI. See [`deploy/README.md`](deploy/README.md).

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/harness.md`](docs/harness.md) | The `ArenaBot` contract, how our evolutionary harness works, **safety and sandboxing**, and how to build your own harness |
| [`docs/verification.md`](docs/verification.md) | The verified tier: hidden seeds, the verifier's scheduler, the AutoAscend floor, what's public |
| [`docs/contributing.md`](docs/contributing.md) | Dev setup, tests, conventions, PR flow |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Symptom → cause → fix |
| [`docs/local-stack.md`](docs/local-stack.md) | Running a full local hub + arena + mutator stack |
| [`deploy/README.md`](deploy/README.md) | Operating the production hub |

## Contributing

Bug reports, bots, and harnesses are all welcome — and you do not need to be
good at NetHack for any of them. Start with
[`docs/contributing.md`](docs/contributing.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE). AutoAscend, vendored at
`src/nethackers/roots/autoascend/` and so shipped inside the wheel, carries its
own MIT license — see
[`LICENSE`](src/nethackers/roots/autoascend/LICENSE), which travels with the
tree wherever it goes.
