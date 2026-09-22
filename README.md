<pre align="center">
 _  _     _   _  _         _              
| \| |___| |_| || |__ _ __| |_____ _ _ ___
| .` / -_)  _| __ / _` / _| / / -_) '_(_-&lt;
|_|\_\___|\__|_||_\__,_\__|_\_\___|_| /__/
solving nethack, many stupid harnesses at a time
</pre>

<p align="center"><a href="https://nethackers.dunnolab.ai"><code>nethackers.dunnolab.ai</code></a></p>

<p align="center">
<a href="https://nethackers.dunnolab.ai"><img src="docs/assets/dashboard.png" width="620" alt="The nethackers dashboard: a table of causes of death across evolved bots above a tombstone that reads: no program has ascended NetHack 3.6.6 yet. Will yours be the first?"></a>
</p>

NetHackers is an open effort to write the first program that wins NetHack.
This is the CLI and the hub behind the site. You write a bot, or point a
coding agent at one. It is scored on fixed seeds in a sandbox on your machine
and re-scored by us on seeds nobody has seen. The hub keeps a link to every
registered program, your repo at an exact commit, for anyone to pull,
improve, and register again; the best per starting character are the elites
it shows first.

## Install

```bash
uv tool install nethackers    # or: pip install nethackers
nethackers setup
```

Python 3.11+. `setup` shows a plan and asks once: logins first (GitHub,
`gh`, your coding agent), then whatever one command installs without `sudo`,
the container runtime, and the sandbox images (about 1 GB). Anything that
needs `sudo` or a click is printed for you. It is safe to run again, and
`--for eval` sets up only that much. `nethackers doctor` says what the
machine can do and what to fix. Per OS: [`docs/setup.md`](docs/setup.md).

## Use

```bash
nethackers                 # the dashboard
nethackers eval ./my-bot --objective val-dwa-law-fem
nethackers evolve val-dwa-law-fem --seed autoascend --operator codex
nethackers submit ./my-bot --objective val-dwa-law-fem
nethackers pull github.com/<someone>/nethacker@<commit> ./bot
```

| command | what it does |
|---|---|
| `eval` | scores a bot on the objective's 15 public seeds, in the sandbox |
| `evolve` | hands the current best bot to the coding agent, one focused change per iteration, and keeps what improves; every evaluated candidate is pushed to your public `nethacker` repo and registered (`--offline` to skip) |
| `submit` | evaluates a bot you already have, pushes it to `github.com/<you>/nethacker`, registers it |
| `pull` | fetches anyone's program at the exact commit that was measured |
| `frontier` `board` `elites` `show` `search` | what the dashboard shows; JSON when piped |

A bot is a directory with a `bot.py` that defines `make_agent()`; that is
the whole contract ([`docs/harness.md`](docs/harness.md)). An objective is
one of the 73 starting identities (`role-race-align-gender`), a role
(`val`), a comma list, or a glob (`val-*-law-*`). The coding agent is
Claude Code, Codex, or OpenCode.

## Scores

| | Public Dungeons | Private Dungeons |
|---|---|---|
| seeds | 15 published per identity | secret |
| run by | you, on your machine | our verifier, on our hardware |
| answers | how good is the bot on seeds it could tune against | does that transfer |

The website shows Private first. All scores come from the pinned
`linux/amd64` arena image; other hosts emulate it (`setup` turns Rosetta on
for Apple Silicon), because the same seed plays a different game on another
architecture. [`docs/verification.md`](docs/verification.md)

## This runs code you didn't write

`eval` runs a `bot.py` that may be a stranger's, `evolve` runs a coding
agent unattended with its permission prompts off, and `pull` puts a
stranger's code on your disk. The evaluator is a sealed container (no
network, read-only root, no capabilities, non-root, resource caps), fetching
accepts only `github.com/<owner>/<repo>@<commit>` over https, and the agent
runs in its own capped container with a timeout. A bot can still influence
its self-reported score, which is why the private tier exists, and the
threat model is a runaway agent rather than a determined adversary.
[`docs/harness.md`](docs/harness.md#3-safety-and-sandboxing)

## Docs

| doc | covers |
|---|---|
| [`docs/setup.md`](docs/setup.md) | what `setup` does on each OS, and how tested it is |
| [`docs/harness.md`](docs/harness.md) | the bot contract, our harness, safety, building your own |
| [`docs/verification.md`](docs/verification.md) | the private tier: hidden seeds, the verifier, the AutoAscend floor |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | symptom, cause, fix |
| [`docs/contributing.md`](docs/contributing.md) | dev setup, tests, PRs for the CLI, hub, arena, and docs |
| [`docs/local-stack.md`](docs/local-stack.md) | a full local hub, arena, and mutator |
| [`deploy/README.md`](deploy/README.md) | running the production hub |

Apache-2.0. AutoAscend ships inside the package under its own MIT license
([`src/nethackers/roots/autoascend/LICENSE`](src/nethackers/roots/autoascend/LICENSE)).
