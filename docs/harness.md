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
two episodes, so nothing carries over. Observations map public NLE keys to
read-only arrays. Actions are integer indices into `nle.nethack.ACTIONS`.
The protocol is `nethackers.contracts.bot.ArenaBot`, a `typing.Protocol`;
there is nothing to import or subclass.

Next to `bot.py`, a manifest. The hub reads `root`, `entrypoint`, `parents`
and `influences`; `schema` and `name` are convention:

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
| the arena | the pinned `linux/amd64` image; `nethackers --version` prints its digest |
| the games | 15 published seeds per identity for Public Dungeons; secret seeds for Private |
| the actions | `nle.nethack.ACTIONS` |
| randomness | `random` and `numpy.random` are seeded per episode; a bot that reads the clock or the network is not replayable |
| time | a per-action timeout inside the arena and a wall-clock ceiling on the box |

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
overall but the best Monk earns and holds the Monk cell.

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
  └─── insert into every cell it improves
```

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
your public `github.com/<you>/nethacker` and registered with the hub,
whether or not it won a cell. The archive records what was tried; a long
run puts many commits under your account. `--offline` skips both.

## Three decisions

**The agent gets code, not commentary.** `/refs/` is real trees: the parent
bot, and each recent attempt as a checked-out tree with its own
`eval.json`, plus a per-identity scores table. The brief states the current
scores, a target to beat, and that a change is kept only if it improves a
cell. One mismatch to know about: the brief says "beat the overall average",
while the loop accepts any candidate that improves a single identity's cell.

**The agent is sealed from its own past.** A fresh container per iteration,
plus each CLI's statelessness flags: `--ephemeral --ignore-user-config` for
Codex, `--no-session-persistence` and no auto-memory for Claude Code;
OpenCode has none and relies on the fresh container. An agent that
remembers yesterday's run converges on the change it already made and
proposes variants of it forever. What it knows of the past is what `/refs/`
shows it. This is not a hermetic boundary: `/workspace` is writable, the
network is open, and Codex's `~/.codex` is mounted read-write.

**The agent experiments on the games it is scored on.** The mutator image
is built from the arena's NLE base for `linux/amd64`, like the arena.
NetHack plays a different game from the same seed on another CPU
architecture; when the mutator ran natively on Apple Silicon, the agent
tuned games the arena never plays. `evolve` refuses to start if the two
images are built for different platforms. The `nethackers` package is not
in the image, only `nethackers.arena` and `nethackers.contracts`: enough to
score a candidate, nothing about how the loop scores it. That is a diet,
not a boundary: the container has network access and the package is on
PyPI. The secret behind the private seeds is in no image at all; its
absence is what protects them.

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
| effort | `--effort` | `-c model_reasoning_effort=` | a variant of the pinned model, so `--effort` needs `--model` |

## Not in this loop

- A held-out gate. The only score that seeds the archive and decides a cell
  is the identity's 15 public seeds. Whether a gain transfers is measured
  separately, by the private tier ([verification.md](verification.md)). If
  you copy this loop, that is a knob you can add.
- Any record of the search. The hub stores programs at `repo@commit` and
  their evidence. How long you searched, on what, is never metered.
- Pruning. Every evaluated candidate is registered, not only the winners.

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
  -v <solution>:/sol:ro -v <tmpdir>:/out \
  --entrypoint timeout ghcr.io/dunnolab/nethackers-arena@sha256:… \
  3600 python -m nethackers.arena.run --solution /sol --out /out/results.json …
```

- No network. The strongest single control.
- A read-only root and a `noexec,nosuid` tmpfs at `/tmp`: the only writable
  path, and nothing written there can be executed.
- No capabilities, no privilege escalation, user `nobody`.
- One core, one GiB and 32 processes per concurrent episode, swap capped to
  memory, so a fork bomb or a memory runaway stays bounded. `timeout` runs
  inside the box and sends SIGKILL after 3600 s per wave of episodes, so
  grandchildren die too.
- The private seeds never enter the box. The secret is expanded to concrete
  per-trajectory seeds on the host and piped in on stdin; argv and the
  environment carry only step and timeout parameters, so there is nothing to
  read out of `/proc`.
- Only `/out/results.json` comes back, through `arena/result_io.py`, which
  refuses a symlink, an oversize file, or anything that is not a JSON list,
  and never executes what the box wrote.

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
docker run --rm \
  --pids-limit 512 --memory 8g --memory-swap 8g --cpus 4 \
  --security-opt no-new-privileges \
  -v <worktree>:/workspace -v <refs>:/refs:ro \
  <mutator-image> timeout 28800 <agent CLI …>
```

- `--pids-limit` is the fork-bomb defence, and the reason this is a
  container rather than a process wrapper: no process-level sandbox
  (Seatbelt, bubblewrap, Landlock, the CLIs' own) caps CPU, memory or
  process count.
- Swap is pinned to memory; otherwise a runaway reaches twice the cap.
- The entrypoint starts as root to remap uids, then drops to a non-root
  `agent` user with `gosu`. It does not `--cap-drop ALL`: the remap needs
  `CAP_SETUID` and `CAP_SETGID` at startup, so this container's posture is
  weaker than the arena's.
- `timeout 28800` (8 hours) sends SIGTERM with no `--kill-after`; it is a
  ceiling only insofar as the CLI honours SIGTERM.
- Instruction-bearing files are stripped from what the agent is handed:
  `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.envrc`, and the `.claude`,
  `.codex` and `.cursor` directories (`harness/refs.py`), so a pulled
  program cannot bring its own agent instructions or MCP servers into your
  run.

Not contained by default:

- Network egress. The CLIs need their model APIs, so nothing restricts it.
  An egress allow-list is designed and off; the credential broker below is
  the one mode that constrains egress. This is the largest hole in the
  default sandbox.
- Your coding-agent credentials. Codex's real `~/.codex` is mounted
  read-write, so code in the cage can influence your next host-side `codex`
  run. Claude Code's credentials file is mounted read-only on Linux, and its
  OAuth token is passed as an environment variable on macOS, visible in
  `docker inspect`. OpenCode's provider keys arrive as environment
  variables. An agent that wanted to exfiltrate them could, and for Codex
  could modify them. `ContainerOperator(broker=True)` swaps the mount for a
  host-side broker (`harness/cred_broker.py`): the container gets a
  placeholder key and a base URL back to the broker, egress is limited to
  it, and the real key is injected per request on the host. It is off by
  default and not wired to a CLI or TUI flag.
- Symlinks. The host-side copies after a run, into the tree store, the next
  `/refs` and the published repository, follow symlinks. Since every
  evaluated candidate is published, a symlink planted in the worktree at a
  host-readable file can end up in a public commit. Not seen in practice; it
  follows from the code.

### Programs you pull

`nethackers pull` clones; nothing runs at clone time, and the next `eval`
runs the tree in the box above. The fetch itself (`hubclient/pull.py`,
`github_ref.py`):

- github.com only, with the host parsed and compared rather than
  substring-matched: `github.com.evil.com`, `github.com@evil`,
  `evil/github.com` and scp forms are refused. The hub refuses a non-GitHub
  reference at registration the same way.
- A locked-down clone, following git's advisory on untrusted repositories
  (GHSA-vm9j-46j9-qvq4): https only (`protocol.allow=never`,
  `protocol.https.allow=always`), `core.symlinks=false`,
  `fetch.recurseSubmodules=false`, `--no-tags`.
- No pin is enforced on the CLI side: `pull owner/name@main` resolves a
  branch. The hub validates registered references as existing 40-hex
  commits, so anything you fetch by way of a board row is pinned; a
  hand-typed ref is whatever you typed.

`linux/amd64` is the reference architecture throughout. Other hosts emulate
it, and a native arm64 score is a different game, which the hub refuses. On
Apple Silicon, Rosetta makes emulation fast: the same 15-episode batch took
823 s under QEMU and 224 s with Rosetta. `nethackers doctor` says whether it
is on.

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

Skip the rest: three agent backends, model discovery, the TUI,
provisioning. Those exist because this harness has to work for strangers.
