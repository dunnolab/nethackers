<pre align="center">
 _  _     _   _  _         _              
| \| |___| |_| || |__ _ __| |_____ _ _ ___
| .` / -_)  _| __ / _` / _| / / -_) '_(_-&lt;
|_|\_\___|\__|_||_\__,_\__|_\_\___|_| /__/
</pre>

<p align="center">
An open effort to build the first program that wins NetHack.<br>
<a href="https://nethackers.dunnolab.ai">nethackers.dunnolab.ai</a> · <a href="https://pypi.org/project/nethackers/">PyPI</a> · Apache-2.0
</p>

<p align="center">
<img src="docs/assets/dashboard.png" width="620" alt="The nethackers dashboard: a table of causes of death across evolved bots (soldier ant, starvation, a wand, a hill orc) above a tombstone that reads: no program has ascended NetHack 3.6.6 yet. Will yours be the first?">
</p>
<p align="center"><sub><code>nethackers</code>, the dashboard, on 22 September 2026. The causes of death are real.</sub></p>

NetHackers is a CLI and a shared hub for programs that play NetHack 3.6.6.
You write a bot, or point a coding agent at one. `nethackers eval` scores it
on fixed seeds in a sandbox. The hub keeps the best program for each of the
73 starting characters, and anyone can pull one, improve it, and register the
result, so your improvement becomes the next person's starting point. How you
produce the program is your business: by hand, with an agent, or with a thing
that builds the thing. The hub never runs your search and never assigns you
work.

## The problem

NetHack came out in 1987 and no program has ever won the version researchers
use. The only autonomous wins on record are three games in 2015, on 3.4.3, by
[BotHack](https://github.com/krajj7/BotHack), whose endgame relied on an
exploit the developers removed that same year. The 2021 NeurIPS challenge ran
more than half a million games and recorded zero ascensions
([Hambro et al., 2022](https://arxiv.org/abs/2203.11889)). Its winner,
[AutoAscend](https://github.com/maciej-sypetkowski/autoascend), is still the
strongest symbolic bot there is, and in 109,545 logged games it reached Medusa
exactly never ([Dungeons and Data, Table 5](https://arxiv.org/abs/2211.00539)).
The best frontier model playing the game directly scores 13% on NetHack in
[BALROG](https://balrogai.com), and 100% on two other games in the same suite.
Humans, for comparison, win about 0.4% of all logged games; experts win around
16% of theirs, and the record streak is 61 wins in a row. The game is
winnable. Nothing we have built wins it.

In the last couple of years, coding agents that write and refine programs in a
loop have cracked problems that resisted everything else: ARC puzzles,
SAT-solver competitions, new provably correct algorithms. The model cannot do
the task, but it can write a program that does. NetHack is where that pattern
has not held yet, and it is a cheap place to test it. The world is open
source, the game is fast to simulate, a policy is a Python file, and a laptop
can run the experiment. Whether it works, nobody knows. That is the fun part.

## Where this stands

As of 22 September 2026: 73 registered programs from 6 people, 0 ascensions.

Progression is a 0-to-1 score for how far a game got, the empirical chance
that a human who reached that point went on to win (BALROG's metric).
AutoAscend, the seed everything here descends from, averages **0.078** over
the 73 identities on the Private Dungeons. The best registered program that
covers all 73 identities scores **0.078**. Per identity, 58 of the 73 are led
by a program, most by a point or two over AutoAscend, and the best single
cells reach **0.167** on private seeds and **0.215** on public ones. The gap
between those two tiers is where the interesting work is. On the public tier,
26 identities have a registered program at all. Live numbers:
[the frontier](https://nethackers.dunnolab.ai/#frontier).

So the honest summary is that the machinery works, and nothing has
meaningfully beaten AutoAscend yet.

## Try it

```bash
pip install nethackers        # or: uv tool install nethackers
nethackers doctor             # what this machine can do, and what to fix
nethackers                    # the dashboard
```

Python 3.11+. Browsing needs nothing else. Scoring a bot needs Docker or
Podman. Evolving one needs `nethackers login` and a coding agent: `claude` or
`codex` logged in on this machine, or OpenCode, which ships inside the
sandbox. Publishing also needs [`gh`](https://cli.github.com/) authenticated
as the same GitHub account.

**Look around.** Every view is a command, and every command prints JSON when
piped.

```
$ nethackers board --scope val
rank  program                                owner   asc  median   mean
----  -------------------------------------  ------  ---  ------  -----
   1  prog_e716dc657e8d9bb4c2aed167710f52ce  Howuhh    0   0.144  0.144
   2  prog_96533958712d16dbd5bb58377e2cf514  Howuhh    0   0.140  0.140
   3  prog_dc05b05efad31410bd0577695b287fca  Howuhh    0   0.135  0.135
```

`frontier` shows how far the community has got on every identity, `elites`
the best program per identity, `show <prog_id>` one program in detail, and
`search --owner <login>` someone's whole history.

**Score a bot.** A bot is a directory with a `bot.py` that defines
`make_agent()`. That is the whole contract; the rest is in
[`docs/harness.md`](docs/harness.md).

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
```

This runs the identity's 15 published seeds in the sandboxed arena and prints
the result. That is your Public Dungeons number, on the same batch everyone
else gets.

**Evolve one.**

```bash
nethackers login
nethackers evolve val-dwa-law-fem --seed autoascend --operator codex --iterations 20
```

Each iteration hands the current elite to the coding agent in a container,
asks for one focused change, smoke-tests the result, scores it on the 15
seeds, and keeps it wherever it improved a cell. `autoascend` is the built-in
seed. The objective can be one identity, a whole role (`val`), a comma list,
or a glob (`val-*-law-*`).

> **Every candidate that gets evaluated is pushed to your public `nethacker`
> repo and registered with the hub, not only the winners.** The archive is
> meant to record what was tried. A long run publishes a lot of commits under
> your name. `--offline` runs the loop without publishing anything.

**Publish a bot you already have.**

```bash
nethackers submit ./my-solution --objective val-dwa-law-fem
```

Evaluates it, pushes it to `github.com/<you>/nethacker`, and registers the
commit.

**Fetch anyone's.**

```bash
nethackers pull github.com/<someone>/nethacker@<commit> ./fetched
```

Everything registered from a public repo is fetchable by anyone, pinned to the
exact commit that was measured.

<details>
<summary>Install notes: Apple Silicon, OpenCode, image sizes</summary>

Two sandbox images, `ghcr.io/dunnolab/nethackers-arena` and `-mutator`, are
pinned by digest in the CLI and pulled on first use. They are several GB, so
the first `eval` takes a while. Nothing needs building.

On Apple Silicon, turn on Rosetta in Docker Desktop (Settings → General →
Apple Virtualization framework → "Use Rosetta for x86_64/amd64 emulation").
The same 15-episode batch on the same machine took 823 s under QEMU and 224 s
with Rosetta. `doctor` reports which one you have.

OpenCode needs nothing installed on the host; its CLI ships in the sandbox.
Give it models by defining providers in `~/.config/opencode/opencode.json`
(or `.jsonc`). Only that file's `provider` section enters the sandbox, along
with the environment variables those providers reference as `{env:NAME}` or
list in `env`, so set each key as `{env:NAME}` or a literal `apiKey`. A
`{file:...}` key, a login made with `opencode2 auth login`, plugins, MCP
servers, and project `opencode.json` files all stay outside. With no key at
all, runs use OpenCode's free models, and `doctor` says so. How each agent
behaves in the sandbox: [`docs/harness.md`](docs/harness.md#the-coding-agents).

`doctor`'s `publish` check covers the hub, your login, and `gh`, but not the
container runtime, so it can report `publish` ready on a machine where
`submit` will still stop at its evaluation.

</details>

## How scoring works

An objective is one of the 73 legal starting identities
(`role-race-align-gender`, like `val-dwa-law-fem`) and a published batch of
15 seeds. A program is scored on how far it gets, averaged over the batch.
Every program carries up to two scores:

| | Public Dungeons | Private Dungeons |
|---|---|---|
| Seeds | 15 published per identity | secret, held by the hub |
| Who ran it | you, on your machine | our verifier, on our hardware |
| Reproducible by you | yes, given a deterministic bot | no |
| What it answers | how good is this bot on seeds it could tune against | does that generalize |

Public is where you work, and it is the intended target: NetHack is unsolved
by such a margin that getting better at the public dungeons is where we expect
the first real gains to come from. Private answers the other question. A
trusted verifier re-runs registered programs on seeds nobody sees and submits
an independent score, so nobody has to take a self-reported number on faith.
The website defaults to Private because it is the harder question. The
mechanism, the seeds, and the verifier's scheduler are in
[`docs/verification.md`](docs/verification.md).

All scores come from one architecture, `linux/amd64`, inside a pinned arena
image. The same seed plays a different game of NetHack on a different CPU, so
a native arm64 score is not comparable and the hub refuses it. Apple Silicon
emulates; with Rosetta it is fast enough (see the install notes above).

One consequence worth stating: the arena has no network, so a bot cannot
call a model while it plays. If you want a language model in the loop at play
time, [BALROG](https://balrogai.com) is the benchmark for that. Here the
model's work happens before the game, in the code it leaves behind.

## This runs code you didn't write

Three things execute code that neither you nor we reviewed. `eval` imports and
runs a `bot.py`, which a stranger wrote if you pulled it. `evolve` runs Claude
Code, Codex, or OpenCode with permission prompts off, unattended, for hours.
`pull` puts a stranger's code on your disk for the next `eval` to run.

The bot evaluator is a sealed box: no network, a read-only root filesystem,
every capability dropped, a non-root user, pid/memory/CPU caps, and the hidden
seeds never enter the container. Fetching accepts only
`github.com/<owner>/<repo>@<commit>`, parsed rather than string-matched, and
clones over https with submodules, symlinks, and tags disabled. The coding
agent runs in its own container with resource limits and a wall-clock timeout,
and instruction-bearing files (`CLAUDE.md`, `AGENTS.md`, `.mcp.json`) are
stripped from the tree it is handed.

What it does not do: a bot can still influence its own score, because the
scorer runs it in-process, which is exactly why the Private tier exists. The
agent's container still has your coding-agent credentials and open network
egress unless you opt into the credential broker. And the threat model is
accident-grade, built for a runaway agent rather than a determined adversary.
If you are evaluating code you have reason to distrust, run it on a machine
you are willing to lose. Details per surface, written to be reused:
[`docs/harness.md`](docs/harness.md#3-safety-and-sandboxing).

## Your code stays yours

We don't run an artifact store, an identity provider, or a code host. GitHub
is all three. A program *is* a `repo@commit`: the hub stores the link, the
manifest, and the scores, never the code, and checks with your own token that
the commit exists before accepting a registration. Your identity is your
GitHub login, through a GitHub App device flow with no client secret anywhere.
Publishing is a push to `github.com/<you>/nethacker`, which you own and can
edit or delete; we hold a pointer. Two limits worth knowing: a link is only
as durable as the repo behind it, and the hub does not check visibility, so a
hand-rolled `register` of a private repo lands on a board and is fetchable by
nobody. `submit` makes the repo public.

## The harness that ships in the box

Our own loop is a MAP-Elites archive over the 73 identities with a coding
agent as the mutation operator: pick a cell, hand its elite to the agent in a
container, ask for one focused change, smoke-test, evaluate on the public
seeds, insert wherever it improved. It optimizes the public seeds on purpose
and has no held-out gate. It is the worked example, not the required path,
and [`docs/harness.md`](docs/harness.md) documents it as something to read
and steal from when you build your own.

## Docs

| Doc | Covers |
|---|---|
| [`docs/harness.md`](docs/harness.md) | the bot contract, how our harness works, safety, building your own |
| [`docs/verification.md`](docs/verification.md) | the Private tier: hidden seeds, the verifier, the AutoAscend floor |
| [`docs/contributing.md`](docs/contributing.md) | dev setup, tests, conventions |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | symptom, cause, fix |
| [`docs/local-stack.md`](docs/local-stack.md) | a full local hub, arena, and mutator |
| [`deploy/README.md`](deploy/README.md) | running the production hub |

## Contributing

Bots and harnesses are the point, and they arrive as registrations, not pull
requests. Pull requests are for the CLI, the hub, the arena, and the docs;
bug reports for anything. You do not need to be good at NetHack for any of
it. Start with [`docs/contributing.md`](docs/contributing.md).

## License

Apache-2.0, see [`LICENSE`](LICENSE). AutoAscend ships inside the package
under its own MIT license, which travels with the tree:
[`src/nethackers/roots/autoascend/LICENSE`](src/nethackers/roots/autoascend/LICENSE).
