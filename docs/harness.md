# The harness

A harness is whatever produces a bot. Ours hands the current best bot to a
coding agent, scores what comes back, and keeps what improves; yours can be
anything. The hub scores programs and never meters the search that made
them. What the sandbox stops and what it doesn't is in
[safety.md](safety.md).

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

Borrow from ours: resource caps on every container that runs code you
didn't write, `--network none` for scoring, sealing the agent from its own
past, a live NLE the agent can score against on the judge's architecture,
and keeping the secret out of every image. Skip the rest: three agent
backends, model discovery, the TUI, provisioning. Those exist because this
harness has to work for strangers.
