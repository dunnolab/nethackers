# The harness

A harness is whatever produces a bot. Ours hands the current best bot to a
coding agent, scores what comes back, and keeps what improves; yours can be
anything. The hub scores programs and never meters the search that made
them. The contract comes first, then how our loop works, then what the
sandbox stops and what it doesn't.

## The contract

A bot is a directory with a `bot.py` that defines `make_agent()`:

```python
# bot.py
from collections.abc import Mapping
from typing import Any


class Bot:
    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        """Start a new episode."""

    def act(self, observation: Mapping[str, Any]) -> int:
        """Return an integer index into nle.nethack.ACTIONS."""
        return 0

    def close(self) -> None:            # optional
        """Release resources after an episode."""


def make_agent() -> Bot:
    return Bot()
```

Each episode runs in its own process: `make_agent()`, then `reset()` with
the first observation, then `act()` until the game ends. No instance sees
two episodes; only `/tmp`, a scratch tmpfs the batch's episodes share,
outlives one. Observations map public NLE keys to read-only arrays. Actions
are integer indices into `nle.nethack.ACTIONS`. The protocol is
`nethackers.contracts.bot.ArenaBot`, a `typing.Protocol`; there is nothing
to import or subclass.

Next to `bot.py`, `nethackers.solution.json`. `eval` never reads it;
`submit` and `evolve` refuse a tree without one. The hub checks that
`parents` and `influences` are lists of strings and stores them as lineage.
It stores `root` and `entrypoint` and acts on neither: the arena always
loads `bot.py` at the tree root. `schema` and `name` are convention:

```json
{
  "schema": "nethackers.solution/v1",
  "name": "autoascend",
  "root": "autoascend",
  "parents": [],
  "influences": [],
  "entrypoint": "bot.py"
}
```

What is fixed:

| | |
|---|---|
| the arena | the pinned `linux/amd64` image; `nethackers --version -o json` prints its digest |
| the games | 15 published seeds per identity for Public Dungeons; secret seeds for Private |
| the actions | `nle.nethack.ACTIONS` |
| randomness | `random` and `numpy.random` are seeded per episode and `PYTHONHASHSEED` is 0; there is no network; a bot that reads the clock or `os.urandom` is not replayable |
| time | 120 s of wall clock per `act()`, per `reset()` and for startup; a ceiling on the whole box of 3600 s times its number of waves, which loses the batch when hit |
| the end | a game ends at 1,000,000 steps, or after 10,000 steps with the turn counter unchanged |
| a failure | a bad action, a timeout, or an exception in the bot zeroes that episode; no retry |

Score it:

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
```

That is the whole surface. `src/nethackers/roots/autoascend/`, the tree
`--seed autoascend` resolves to, is a complete example: AutoAscend, the 2021
NetHack Challenge winner, wrapped to the contract. `bot.py` is a 34-line
shim; the work is `arena_adapter.py`, which turns AutoAscend's blocking
`env.step` loop into the pull-based `act()` with a thread and two queues.
Adapting a bot that owns its own game loop is that inversion, not the
contract.

## How our loop works

`src/nethackers/harness/` is a MAP-Elites loop: an archive with one cell
per identity, each holding the best program on that identity, plus a union
cell for the best program across all of them. A program that is mediocre
overall but the best Monk earns the Monk cell, and holds it until another
program beats it there, or ties it there while winning some other cell.

```
cold-start each cell from the hub's elite, else from --seed
      │
      ▼
  ┌─▶ pick a random cell
  │        │
  │        ▼
  │   mutate its elite ─── coding agent, in a container, with /refs/
  │        │
  │        ▼
  │   smoke gate ──────── 1 episode on a throwaway seed: does it run at all?
  │        │
  │        ▼
  │   dev eval ────────── the 15 published seeds, whole identity set
  │        │
  │        ▼
  │   register ────────── publish + register every evaluated candidate
  │        │
  │        ▼
  └─── insert into every cell it improves (and ties, if it improved one)
```

Each hub elite is re-scored here on the public seeds before it fills a
cell; the union cell starts from the board's first row. `--from-seed` skips
the hub; `--verified` reads the verified tier instead. A run stops after
three consecutive operator failures, and on the first when the operator
refuses the request, for example a model the sandbox's CLI does not know.

| Stage | Where | What it does |
|---|---|---|
| Archive | [`archive.py`](../src/nethackers/harness/archive.py) | One `Cell` per identity plus a `union` cell |
| Selection | [`loop.py`](../src/nethackers/harness/loop.py) | Draws a cell at random (the union cell double-weighted once filled) and takes its elite as the parent |
| Brief | [`brief.py`](../src/nethackers/harness/brief.py) | The agent's prompt: identities, seeds, "make one focused change" |
| Refs | [`refs.py`](../src/nethackers/harness/refs.py) | Read-only `/refs/`: the parent bot, its per-seed results, recent attempts as real trees, a scores table |
| Mutation | [`container_operator.py`](../src/nethackers/harness/container_operator.py) | Runs Claude Code, Codex or OpenCode 2 inside the mutator image against a bind-mounted worktree |
| Smoke gate | [`gate.py`](../src/nethackers/harness/gate.py) | Pass or fail: a mutant missing its manifest or entrypoint, identical to its parent, or unable to finish one short episode |
| Evaluation | [`evaluate.py`](../src/nethackers/harness/evaluate.py) → [`eval/runner.py`](../src/nethackers/eval/runner.py) | The arena container on the objective's batch |
| Registration | [`register.py`](../src/nethackers/harness/register.py) | Reports the batch's evidence for every evaluated candidate, win or not; publishing is a hook in `launch.py` |

```bash
nethackers evolve val-dwa-law-fem --seed autoascend --operator codex --iterations 20
```

Every candidate that passes the smoke gate and gets evaluated is pushed to
your public `github.com/<you>/nethacker`, on a branch per run, and
registered with the hub, whether or not it won a cell. The hub records what
was tried; a long run puts many commits under your account. `--offline`
skips both, and still reads the hub to seed cells; `--from-seed` keeps the
hub out.

## Three decisions

**The agent gets code, not commentary.** `/refs/` is real trees: the parent
bot, and each of the run's last three evaluated attempts as a copied tree
with its own `eval.json`, plus a per-identity scores table. The brief states
the current per-identity scores, the best overall so far, an aim one point
above it, and that a change is kept only if its overall average strictly
beats the best so far; a single-identity objective gets no target and no
keep rule. One mismatch to know about: the brief's rule is the overall
average, while the loop accepts any candidate that improves a single
identity's cell.

**The agent is sealed from its own past.** A fresh container per iteration,
plus each CLI's statelessness flags: `--ephemeral --ignore-user-config
--ignore-rules` for Codex, `--no-session-persistence` and no auto-memory
for Claude Code; OpenCode has none and relies on the fresh container. An
agent that remembers yesterday's run converges on the change it already
made and keeps proposing variants of it. What it knows of the past is what
`/refs/` shows it. This is not a hermetic boundary: `/workspace` is
writable, the network is open, and Codex's `~/.codex` is a writable mount
that outlives the iteration, the broker's cage by default and your real one
under `--no-broker` ([Safety](#the-coding-agent)).

**The agent experiments on the games it is scored on.** The mutator image
is built from the same NLE base image as the arena, for `linux/amd64` only,
like the arena. NetHack plays a different game from the same seed on
another CPU architecture; when the mutator ran natively on Apple Silicon,
the agent tuned games the arena never plays. `evolve` refuses to start if
the two images are built for different platforms. The `nethackers` package
is not installed in the image; only `nethackers.arena` and
`nethackers.contracts` are copied onto `PYTHONPATH`, enough to score a
candidate the way the arena does. The loop, its gate, its seed module and
the hub client are not there. That is a diet, not a boundary: the container
has network access and the package is on PyPI, harness included. The secret
behind the private seeds is in no image at all; its absence is what protects
them.

## Operators

`--operator`, or the form's Operator picker, chooses the coding agent. Each
runs headless in a fresh mutator container per iteration with its approval
prompts off. Logins and OpenCode's provider rules are in
[setup.md](setup.md#coding-agents).

| | Claude Code `claude` | Codex `codex` | OpenCode 2 `opencode2` |
|---|---|---|---|
| approvals off | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | `--auto` |
| kept from its past | `--no-session-persistence`, `--strict-mcp-config`, auto-memory off, `--setting-sources project,local` | `--ephemeral`, `--ignore-user-config`, `--ignore-rules` | the fresh container; project config switched off |
| models | your account's, from Anthropic's API; aliases like `opus` pass | `codex debug models`, run inside the sandbox | `opencode2 models`, run inside the sandbox |
| effort | `--effort` | `-c model_reasoning_effort=` | `--variant` of the pinned model, so `--effort` needs `--model` |

`nethackers models --operator <name>` lists what each sandbox serves. For
Claude the picker's list is your account's, and the CLI pinned inside the
image resolves `--model` again against its own: an id newer than that CLI
passes the picker and fails at the first iteration with ``unrecognized model
'<id>' — the sandbox's CLI doesn't know that id; use an alias like `opus`, or
update the CLI in the mutator image``, and the run stops there. Aliases never
go stale. For Codex and OpenCode a pinned `--model` is checked against the
sandbox's catalog before the run starts and refused there; a model whose
catalog could not be read, or one the catalog lists and the CLI then
rejects, is an ordinary failure and takes three tries. `nethackers models`,
the picker and that check run the CLI's catalog command in a probe
container with the credential mounted, broker or not; nothing of a program
runs there. The CLI versions are pinned in `Dockerfile.mutator`; a
bump re-pins the image. The operator id stays `opencode2` while the binary
inside the image is `opencode`, with an `opencode2` symlink.

## Not in this loop

- A held-out gate. The only score that seeds the archive and decides a cell
  is the identity's 15 public seeds. Whether a gain transfers is measured
  separately, by the private tier ([verification.md](verification.md)). If
  you copy this loop, that is a knob you can add.
- A record of the search on the hub. The hub stores each program at
  `repo@commit`, its evidence and its parent. Iterations, tokens, model and
  operator stay in the local run folder (`runs/<id>/run.json`,
  `metrics.jsonl`, `logs/`) and never reach the hub.
- Pruning. Every evaluated candidate is registered, not only the winners;
  smoke-gate rejects are never scored and never registered.

## Safety

Three commands run other people's code. `eval` imports a `bot.py` that may
be a stranger's. `evolve` runs a coding agent unattended for hours with its
permission prompts off. `pull` puts a stranger's repository on your disk.
The controls below contain accidents: a fork bomb, a memory runaway, an
agent that deletes outside its worktree, a bot that phones home. They do
not stop a kernel exploit; there is no microVM or gVisor here. Running
strangers' programs on a machine that matters, or a public verifier, means
treating the container as a limit on the blast radius, not as a wall.

### The bot

`nethackers eval` runs the arena as a sealed box (`eval/runner.py`, flags
from `sandbox_flags.offline_flags`). Caps are sized from the container
runtime for the number of episodes the box runs at once:

```
<docker|podman> run --rm -i --platform linux/amd64 \
  --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit <32 per episode> \
  --memory <three quarters of the runtime's memory> --memory-swap <the same> \
  --cpu-period 100000 --cpu-quota <100000 per episode> \
  --user 65534:65534 \
  -e HOME=/tmp -e PYTHONWARNINGS=ignore::RuntimeWarning \
  -v <solution>:/sol:ro -v <tmpdir>:/out \
  --entrypoint timeout ghcr.io/dunnolab/nethackers-arena@sha256:… \
  <3600 × waves> python -m nethackers.arena.run --solution /sol \
    --max-steps … --no-progress-timeout … --action-timeout … \
    --max-parallel-evals <episodes> --out /out/results.json
```

`--platform` is passed only for the pin; an override image runs without it.
Also `--name` and `--label`, as for the coding agent.

- No network. The strongest single control.
- A read-only root. Two paths are writable: a `noexec,nosuid` tmpfs at
  `/tmp`, where a written file cannot run as a binary (an interpreter can
  still read it), and the `/out` bind mount that carries the result file
  back, a directory under `~/.nethackers/tmp` created world-writable so
  `nobody` can write it on native Linux and removed when the eval ends.
- No capabilities, no privilege escalation, user `nobody`.
- One core and 32 processes per concurrent episode, three quarters of the
  runtime's memory for the whole box (one GiB per episode is the budget that
  decides how many run at once; 8 episodes, and one GiB each, when the
  runtime cannot be asked), swap capped to memory, so a fork bomb or a
  memory runaway stays bounded.
  `timeout` runs as PID 1 inside the box and sends SIGTERM to the whole
  process group after 3600 s times the number of waves, with no
  `--kill-after`; workers and bots are armed with `PR_SET_PDEATHSIG`
  (`arena/lifetime.py`), and whatever survives dies with PID 1, so
  grandchildren die too.
- The secret behind the private seeds never enters the box. It is expanded
  to concrete per-trajectory seeds on the host and piped in on stdin; argv
  carries only the step, timeout and parallelism parameters and the two
  mount paths, the environment adds `HOME=/tmp` and a warnings filter, so
  nothing in `/proc` names a seed or the secret.
- Only `/out/results.json` comes back, through `arena/result_io.py`, which
  refuses a symlink, a file over 8 MB, or anything that is not a JSON list,
  and never executes what the box wrote. A bot's traceback text, up to
  8,000 characters, lands in the result's `error` field, as data.

Inside the box, `arena/sandbox.py` runs the bot in its own subprocess,
clears the write flag on observation arrays, and enforces a per-action
timeout.

Two limits:

- A bot can influence its own score. The arena puts the solution directory
  at the front of `sys.path` so `import bot` resolves, then imports NLE. A
  solution that ships modules named like the scorer's imports is importable
  by the scorer, in-process. Sealing the container does not change that; it
  happens inside the box. A self-reported number is a claim, which is why
  the private tier exists.
- The digest pin is a default. `--image`, `NETHACKERS_ARENA_IMAGE`, or a
  checkout's `.env.stack` still win, so a local eval can run unpinned bytes.
  Evidence records the digest it ran, and the hub refuses to register
  anything it cannot classify at the current arena major, so an unpinned
  image can produce a local score but never a submittable one.

### The coding agent

`nethackers evolve` runs the agent with its permission prompts disabled,
because it runs unattended and cannot stop to ask. That is defensible only
because the CLI runs inside a container (`harness/container_operator.py`):

```
docker run --rm --platform linux/amd64 \
  --pids-limit 512 --memory 8g --memory-swap 8g --cpus 4 \
  --security-opt no-new-privileges \
  -v <worktree>:/workspace -v <refs>:/refs:ro \
  <the operator's route to the broker, from harness/auth_inject.py> \
  <mutator-image> timeout 28800 <agent CLI …>
```

The route is a placeholder token plus `ANTHROPIC_BASE_URL` for Claude, an
empty writable `~/.codex` plus `CODEX_HOME` for Codex, whose route is a
provider override in its own command, and a rewritten copy of the provider
section for OpenCode. Also `--name`, `--label`, `-w /workspace`, and `-i`
for OpenCode. On the broker path, on every host,
`--add-host host.docker.internal:host-gateway`: native Linux needs it to
name the host, Docker Desktop resolves the name anyway. `--platform` is
passed only for the pin, as for the arena. Rootless Podman adds
`--userns=keep-id --user 0`. These caps are fixed, unlike the arena's.

- `--pids-limit` is the fork-bomb defence, and the reason this is a
  container rather than a process wrapper: process-level sandboxes cap
  neither CPU, nor memory, nor process count.
- Swap is pinned to memory; otherwise a runaway reaches twice the cap.
- The entrypoint starts as root to remap uids, then drops to a non-root
  `agent` user with `gosu`. It does not `--cap-drop ALL`: the remap and the
  drop need `CAP_CHOWN`, `CAP_SETUID` and `CAP_SETGID` at startup, so this
  container's posture is weaker than the arena's.
- `timeout 28800` (8 hours) sends SIGTERM with no `--kill-after`; it is a
  ceiling only insofar as the CLI honours SIGTERM. A stop from the CLI or
  the TUI is `docker kill`, which does not depend on it.
- Instruction-bearing files are stripped, at every level, from both the
  worktree and `/refs`: `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.envrc`,
  `.cursorrules`, `opencode.json`, `opencode.jsonc`, and the `.claude`,
  `.codex`, `.cursor`, `.vscode` and `.opencode` directories
  (`harness/refs.py`), so a pulled program's agent config files are not
  loaded. It is a list of names: a README or a code comment still reaches
  the agent, and a file not on the list passes through. Codex also runs
  with its user config and rules off, and OpenCode with its project config
  off; Claude Code drops only its user settings layer, so for it the strip
  is what keeps a tree's config out.
- Your model credential never enters the agent's container, for Claude and
  Codex and for the OpenCode providers the broker can take. A credential broker
  on the host (`harness/cred_broker.py`) is on by default for every
  operator: the container gets a placeholder, or for Codex no credential at
  all, plus a route back to the broker, and the broker injects the real
  credential on the wire, per request, to the one real provider. The
  per-operator detail is under [The credential broker](#the-credential-broker)
  below. `--no-broker`, or the form's Credential toggle, opts back into
  mounting the credential, which is the exposure described next.

Not contained by default:

- Network egress. The CLIs need their model APIs, so nothing restricts it:
  no `--network` flag in any mode, and no egress allow-list exists. The
  broker keeps the credential on the host; it does not keep the container
  off the network. This is the largest hole in the default sandbox.
- The broker itself. It is an unauthenticated relay to one upstream whose
  only check is the Host header, so while a run is live any process on the
  host, and on Linux any container on the bridge the `ufw` rule opens, can
  spend through it.
- Codex's cage. On the broker path Codex gets `~/.nethackers/codex-cage`,
  world-writable and mounted read-write at `~/.codex`; only its `auth.json`
  and `config.toml` are cleared, so it persists across iterations and runs,
  a writable channel from one iteration to the next.
- Your coding-agent credentials, under `--no-broker`. Then Codex's real
  `~/.codex` is mounted read-write, so code in the container can influence
  your next host-side `codex` run; Claude Code's credentials file is mounted
  read-only on Linux, and its OAuth token is passed as an environment
  variable on macOS, visible in `docker inspect` and in the host process
  list; OpenCode's provider keys arrive as forwarded environment variables,
  or inside the read-only copy of the provider section when `opencode.json`
  holds the key literally. An agent that wanted to exfiltrate them could,
  and for Codex could modify them.
- Symlinks. The host-side copies after a run, into the tree store, the next
  `/refs` and the published repository, follow symlinks. Since every
  evaluated candidate is published unless the run is `--offline`, a symlink
  planted in the worktree at a host-readable file can end up in a public
  commit. Not seen in practice; it follows from the code.

### The credential broker

Per operator (`harness/auth_inject.py`):

- Claude Code. The broker adds the OAuth bearer. A setup-token in
  `NETHACKERS_CLAUDE_SETUP_TOKEN` or `~/.nethackers/claude/setup-token`
  wins when present; otherwise the broker reads your `claude` login and, at
  the start of an iteration with under 30 minutes left on the roughly
  eight-hour token, refreshes it on the host and writes the rotated pair
  back to the Keychain or `~/.claude/.credentials.json`, so you log in
  once. A host whose refresh `platform.claude.com` refuses fails that
  iteration and says to provision a setup-token.
- Codex. Its provider is overridden in its own command to point at the
  broker, which adds the bearer and the account id, refreshes the rotating
  token on the host when it nears expiry and writes it back to
  `~/.codex/auth.json`, and forwards through `curl_cffi` with Chrome TLS
  impersonation, since that upstream sits behind Cloudflare. `curl_cffi` is
  installed into nethackers' own interpreter on demand, never into the
  sandbox or `uv.lock`.
- OpenCode. One broker per provider whose key is a literal or a set
  environment variable and whose upstream is known, an explicit `baseURL`
  or a provider named `anthropic` or `openai`. Any other provider keeps the
  mount behaviour, its key in the mounted copy or forwarded as an
  environment variable, and a config with no brokerable provider mounts
  without a message.

Where it listens: on the loopback on macOS and, on any other host, on the
gateway of the runtime's `bridge` network (172.17.0.1 by default), never on
all interfaces, on a free port in 11700 to 11749, an ephemeral one when all
fifty are busy. When
that gateway cannot be read, under Podman for one, it falls back to the
loopback and the run fails as below. A `ufw` host needs the one rule
`nethackers setup` prints.

When the sandbox cannot reach it: an agent that exits non-zero before one
request reaches the broker makes the run print that, with the rule and
`--no-broker` as the alternative; the loop counts it as an operator failure
and stops after three. Nothing falls back to a mount. On macOS the hint is
not printed, and an agent that exits 0 without ever calling the broker is
not flagged.

Claude and Codex ran through the broker live on macOS and on an Ubuntu box
before release; OpenCode did not. `make broker-e2e` runs the real CLIs in
the real image against a mock provider, with a probe that looks for the
credential in the environment, the mounts and a direct call; `make
broker-live` repeats the three round-trips with your own logins. Both are
gated and never run in CI.

### Programs you pull

`nethackers pull` clones; nothing runs at clone time, and the next `eval`
runs the tree in the box above. The fetch itself (`hubclient/pull.py`,
`github_ref.py`):

- github.com only, with the host parsed and compared rather than
  substring-matched: `github.com.evil.com`, `github.com@evil`,
  `https://evil/github.com/…` and scp forms are refused. The hub refuses a
  non-GitHub reference at registration the same way.
- A locked-down clone. Git's advisory on untrusted repositories
  (GHSA-vm9j-46j9-qvq4, CVE-2024-32465) is why the tree is treated as
  hostile; the flags are ours: https only (`protocol.allow=never`,
  `protocol.https.allow=always`), `core.symlinks=false`,
  `fetch.recurseSubmodules=false`, `--no-tags`.
- No pin is enforced on the CLI side: `pull owner/name@main` resolves a
  branch, and a short sha resolves too; a tag does not, since tags are not
  fetched. The hub validates registered references as existing full 40-hex
  commits, checked against GitHub with your own token, so anything you
  fetch by way of a board row is pinned; a hand-typed ref is whatever you
  typed.

`linux/amd64` is the reference architecture throughout. Other hosts emulate
it, and a native arm64 score is a different game, which the hub refuses:
only the amd64 image digests it has classified are admitted. On Apple
Silicon, Rosetta makes emulation fast: on one machine, the same 15-episode
batch took 823 s under QEMU and 224 s with Rosetta. `nethackers doctor`
says whether it is on.

## Your own harness

- Conform to the contract: `bot.py`, `make_agent()`, integer actions.
- Score with `nethackers eval` on the pinned image. Evidence for the hub has
  to come from that image; a self-built one is unclassified and the hub
  refuses it.
- Register with `nethackers submit ./my-bot --objective …`, which publishes
  to `github.com/<you>/nethacker` and registers, or with
  `nethackers register --repo github.com/you/name --commit <sha> --evidence eval.json`
  for a tree you already pushed.
- Start from an elite. `nethackers elites --scope <identity>` names the best
  program; `nethackers pull` fetches it. Building on someone else's program
  is the intended path.

If it runs code you didn't write, the parts of ours to copy:

1. Cap every container: processes, memory plus swap, CPU, and a wall clock.
2. Score with `--network none`.
3. Keep untrusted code off the scorer's `sys.path`, or accept that a
   submission can reach your scoring logic. This is the one we got wrong.
4. Seal the evaluator: a non-root user, `--cap-drop ALL`, a read-only root
   with a `noexec,nosuid` scratch, `no-new-privileges`.
5. Treat self-reported numbers as claims. An independent re-run on seeds the
   author never saw is the only score to rank on.
6. Keep secrets out of the box: expand them on the host and pipe only the
   concrete values over stdin, never through argv or the environment. And
   constrain what a fetch can be: parse the host, allow one transport,
   disable submodule and symlink checkout.
7. Keep the model credential out of the agent's container: a host-side
   proxy that injects it on the wire, a placeholder or nothing inside, and a
   fixed port range you can firewall to. Ours checks only the Host header;
   add a per-run secret if the host has other tenants.

Skip the rest: three agent backends, model discovery, the TUI,
provisioning. Those exist because this harness has to work for strangers.
