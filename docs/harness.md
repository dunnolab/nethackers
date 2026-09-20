# The harness

```
   _  _ ___ _____ _  _   _   ___ _  _____ ___  ___
  | \| | __|_   _| || | /_\ / __| |/ / __| _ \/ __|
  | .` | _|  | | | __ |/ _ \ (__| ' <| _||   /\__ \
  |_|\_|___| |_| |_||_/_/ \_\___|_|\_\___|_|_\|___/
                                    the harness
```

A **harness** is whatever produces a bot. Ours is an evolutionary loop driven by
a coding agent; yours can be anything at all.

> **Two things to know about ours before you read it.**
>
> **It was built as an example, from the first commit.** It is not our best
> guess at the strongest possible search — it is the reference implementation,
> written so that people arriving from very different backgrounds can run
> something real on day one. A lot of what looks like complexity in here is
> breadth, not depth: a multi-arch mutator image because contributors are on
> Apple Silicon and Linux (the arena instead pins one reference architecture —
> more below); two coding-agent backends because people already have one
> or the other; auto-provisioning, preflight checks, and a TUI because "install
> Docker and compile NLE" is where most people would otherwise stop. Borrow the
> **design decisions**; you almost certainly do not need the surface area.
>
> **It optimizes against Public Dungeons, on purpose.** The only score in the
> loop — the one that seeds the archive and decides whether a mutation earns a
> cell — is the identity's 15 published seeds. There is no held-out gate, and the
> loop is not trying to have one. The starting bet is that NetHack is far enough
> from solved that *simply getting better at the public dungeons* is where the
> first generalizable improvements come from: the competence still missing is
> basic enough that you don't need held-out pressure to find it. Measuring whether
> that transfers is a separate job, done independently by the Private Dungeons
> tier ([`verification.md`](verification.md)) — the loop doesn't duplicate it.

This document is in three parts:

1. [**The contract**](#1-the-contract) — the only thing you *must* satisfy.
2. [**How our harness works**](#2-how-our-harness-works) — the worked example,
   with pointers into the source.
3. [**Safety and sandboxing**](#3-safety-and-sandboxing) — what we isolate, what
   we don't, and what to copy if you build your own.

Then: [building your own](#4-building-your-own-harness).

---

## 1. The contract

The platform evaluates **programs**, and never meters the search that made one.
It does not care whether your bot came from a coding agent, a genetic algorithm,
a weekend of hand-written heuristics, or a search you invented. It cares about
exactly one interface.

A solution is a directory containing `bot.py` with a top-level `make_agent()`:

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

Each episode runs in its own process, which calls `make_agent()`, then `reset()`
with the initial observation, then `act()` until the episode ends. No bot instance
ever sees two episodes, so you cannot carry state across them. Observations are
mappings of public NLE observation keys to **read-only** values — arrays arrive
with the write flag cleared. Actions must be integer indices into
`nle.nethack.ACTIONS`.

The protocol is `nethackers.contracts.bot.ArenaBot`
([`src/nethackers/contracts/bot.py`](../src/nethackers/contracts/bot.py)) — a
`typing.Protocol`, so you do not import or subclass anything to conform.

Alongside `bot.py`, a solution carries a manifest. The hub reads `root`,
`entrypoint`, `parents`, and `influences`; `schema` and `name` are convention
rather than validated fields:

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

`src/nethackers/roots/autoascend/` (the tree `--seed autoascend` resolves to) is a complete worked example: AutoAscend (the 2021 NetHack
Challenge winner) wrapped to the contract. `bot.py` is a 34-line shim, but the
real work is `arena_adapter.py` (~140 lines), which inverts AutoAscend's blocking
`env.step` loop into the pull-based `act()` the contract wants, using a thread and
two queues. If you are adapting an existing bot that owns its own game loop, that
inversion — not the contract — is your actual task.

To score it:

```bash
nethackers eval ./my-bot --objective val-dwa-law-fem
```

That's the whole surface area. Everything below is optional.

---

## 2. How our harness works

Our harness lives in [`src/nethackers/harness/`](../src/nethackers/harness/). It
is a **MAP-Elites** loop: rather than hill-climbing a single champion, it keeps
an archive with one cell per identity, each holding the best program on *that*
identity. Illumination, not a scalar climb — a program that is mediocre overall
but excellent as a Monk still earns and holds its cell.

**What it is optimizing.** Cold-start seeding and the dev eval that decides
whether a mutation earns a cell are both Public Dungeons numbers: the identity's
15 published seeds. That is the whole objective, deliberately — the loop optimizes
one legible thing and leaves the question of transfer to a tier that measures it
independently.

**There is no held-out gate in this loop**, by design. `harness/seeds.py` still
exports a `validation_spec` over a reserved seed range and its docstring still
calls it "the evolver's own overfit gate", but that gate was removed: the only
remaining caller builds the one-episode smoke spec below, and the run's
`best_held` telemetry is a hard-coded `0.0`. Nothing re-scores a candidate on
unseen seeds before accepting it. If you copy this loop, that is a knob you can
add — not a bug you need to fix.

### The loop

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
  │   register ────────── publish + register EVERY evaluated candidate
  │        │
  │        ▼
  └─── insert into every cell it improves
```

| Stage | Where | What it does |
|---|---|---|
| Archive | [`archive.py`](../src/nethackers/harness/archive.py) | One `Cell` per identity plus a `union` cell for the best full-coverage program |
| Selection | [`loop.py`](../src/nethackers/harness/loop.py) | Draws a cell at random (the `union` cell double-weighted once filled) and takes its elite as the parent — no score steers this |
| Brief | [`brief.py`](../src/nethackers/harness/brief.py) | Builds the agent's prompt: identities, seeds, "make one focused change" |
| Refs | [`refs.py`](../src/nethackers/harness/refs.py) | Assembles read-only `/refs/`: the parent bot, its per-seed results, recent attempts as real code trees, a scores table |
| Mutation | [`container_operator.py`](../src/nethackers/harness/container_operator.py) | Runs Claude Code, Codex or OpenCode 2 inside the mutator image against a bind-mounted worktree ([the coding agents](#the-coding-agents)) |
| Smoke gate | [`gate.py`](../src/nethackers/harness/gate.py) | Pass/fail, not a score: rejects a mutant missing its manifest/entrypoint, identical to its parent, or unable to finish one short episode on a throwaway seed |
| Evaluation | [`evaluate.py`](../src/nethackers/harness/evaluate.py) → [`eval/runner.py`](../src/nethackers/eval/runner.py) | Runs the arena container on the objective's batch |
| Registration | [`register.py`](../src/nethackers/harness/register.py) | Reports the whole batch's evidence for **every** evaluated candidate, win or not (publishing itself is a separate hook in `launch.py`) |

Run it:

```bash
nethackers evolve val-dwa-law-fem --seed roots/autoascend --operator codex --iterations 20
```

> **Register-all.** Note the last two rows: publish and register happen *before*
> the archive decides whether the candidate improved on anything. Every mutant
> that passes the smoke gate and gets evaluated is pushed to your public
> `nethacker` repo and registered with the hub — not just the winners. That is
> deliberate (the archive records what was tried, which is the point of keeping
> the whole search visible), but it means a long run puts a lot of commits under
> your GitHub account. `--offline` skips both.

### The coding agents

`--operator` (or the evolve form's Operator picker) chooses one of three
coding-agent CLIs. Each runs headless in a fresh mutator container per iteration,
with its approval prompts turned off ([§3](#b-the-coding-agent)). They differ in
how they log in, how much of your own setup they see, and how a model and
reasoning effort are pinned.

| | Claude Code (`claude`) | Codex (`codex`) | OpenCode 2 (`opencode2`) |
|---|---|---|---|
| Log in on the host | run `claude` | `codex login` | nothing to log in to: providers come from your global `opencode.json` ([below](#opencode-2)) |
| What enters the sandbox | the credential: `~/.claude/.credentials.json` read-only on Linux, the Keychain OAuth token as `CLAUDE_CODE_OAUTH_TOKEN` on macOS | your real `~/.codex`, **read-write** (its refresh tokens rotate, so it can't be a copy) | a read-only copy of the `provider` section of your global config, plus the env vars it names |
| Approvals off | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | `--auto` |
| Kept from its own past | `--no-session-persistence`, `--strict-mcp-config`, auto-memory off, and `--setting-sources project,local`: your user settings are dropped, the worktree's project settings still load | `--ephemeral`, `--ignore-user-config`, `--ignore-rules` | no such flags exist; its session store lives only in the throwaway container, and project config is switched off |
| Model list | your account's models, from Anthropic's API on the host; aliases like `opus` always pass | `codex debug models`, run inside the sandbox so it matches the CLI there | `opencode2 models`, run inside the sandbox |
| Reasoning effort | `--effort` | `-c model_reasoning_effort=` | a variant of the pinned model, `provider/model#variant` |

#### OpenCode 2

OpenCode 2 is provider-agnostic, so unlike the other two there is no single
account to reuse. The sandbox's view of it is built from one file: your global
`~/.config/opencode/opencode.json` or `opencode.jsonc` (under `$XDG_CONFIG_HOME`
when that is set).

**What crosses into the sandbox.** Only that file's `provider` section, copied to
an owner-only file under `~/.nethackers/opencode2/` and mounted read-only in place
of the original, plus the environment variables those providers name as
`{env:NAME}` or list in their `env` field. Variables are forwarded by name, so
their values never appear on the `docker run` command line. Your plugins, MCP
servers, instructions and agents stay on the host.

**What doesn't cross:**

- **Logins made with `opencode2 auth login`**, a ChatGPT Plus/Pro login included.
  OpenCode 2 keeps them in its own database, which the sandbox never sees, and a
  subscription login renews itself: renewing a copy inside the sandbox would
  invalidate the one on your machine. For GPT on a ChatGPT subscription, use the
  `codex` operator.
- **A `{file:...}` key.** The file isn't in the container. Use `{env:NAME}` or a
  literal `apiKey`.
- **Project config** — `opencode.json` files and `.opencode/` directories. The
  worktree is a copy of someone else's program, and OpenCode trusts project config
  completely: it can point a provider, with the forwarded key, at another server,
  and it loads plugins and starts MCP servers. Every run and every model check
  sets `OPENCODE_DISABLE_PROJECT_CONFIG=1`.

**Without a key**, OpenCode serves a handful of free `opencode/*` models, and
`doctor` and the evolve form say "free models only". Their availability is
OpenCode's to decide: on 2026-09-14 some answered in two seconds and others never
replied, and an iteration on a model that never replies waits out the 8-hour
sandbox timeout.

**Custom providers** show up in the model picker as `provider/model`, with their
declared `variants` as the effort choices. The key's variable has to be exported
in the shell that launches nethackers:

```jsonc
{
  "provider": {
    "myllm": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://llm.example.com/v1",
        "apiKey": "{env:MYLLM_API_KEY}"
      },
      "models": {
        "my-model": { "variants": { "high": { "reasoningEffort": "high" } } }
      }
    }
  }
}
```

Inside the sandbox `localhost` is the container itself, so a model server running
on your own machine needs `http://host.docker.internal:PORT/v1` under Docker
Desktop. Plain Docker on Linux doesn't provide that name.

**Reasoning effort is a model variant.** OpenCode expresses reasoning effort as
a per-model *variant* (the `--variant` flag, e.g. high/max), not a free-standing
effort flag, so an effort needs a pinned model: `evolve --operator opencode2`
refuses `--effort` without `--model`, and the evolve form offers no effort level
while the model is "Harness default". The chosen effort is passed straight
through as the model's `--variant` (a legacy `provider/model#variant` string is
accepted too and split into the same pair).

**Two mechanics differ from the other agents:**

- **The brief goes in on stdin** (`docker run -i`), not as an argument:
  `opencode2 run` wraps an argument containing spaces in quotes and escapes its
  inner quotes, which broke the JSON commands in the brief.
- **The model list is read once it settles.** For its first few seconds in a
  fresh container `opencode2 models` prints nothing, so the check polls until two
  readings agree — about 3 seconds, 15 at most.

**It's pinned.** The image installs one exact build, `opencode-ai@1.18.31`, next
to pinned Claude Code and Codex versions. Upstream renamed the binary
`opencode2` → `opencode` in this line; the sandbox keeps the `opencode2` name
via a symlink so the operator id is stable. Moving to a newer build is an edit to
`Dockerfile.mutator`, which rebuilds the image. Everything above was checked
against that build.

### Two design choices worth stealing

**The agent gets code, not commentary.** `/refs/` is a directory of *real trees* —
the parent bot, and each recent attempt as a complete checked-out tree with its own
`eval.json` — plus a per-identity scores table. Showing an agent the actual diffs
that did and didn't work beats describing them.

The brief is candid rather than coy: it tells the agent its current per-identity
scores, a target to beat, and that a change is kept only if it raises the overall
average. That is a real tradeoff, not an oversight — being explicit about the
objective gets more focused changes, at the cost that the agent is optimizing a
number it has been shown. (One wrinkle if you copy this: the criterion the brief
states — beat the overall average — is stricter than what the loop actually does,
which accepts any candidate that improves *any single* identity's cell.)

**Seal the operator from its own past, not just from the evaluator.** This is the
one lesson we'll spoil for free. A coding agent that remembers yesterday's run
stops exploring: it converges on the change it already made and re-proposes
variants of it forever. Diagnosing a stalled loop as "the mutator is broken" when
it is actually "the parent is frozen and the mutations are redundant" costs days.

Our seal is a fresh container per iteration plus per-CLI statelessness flags
(`--ephemeral --ignore-user-config` for Codex; OpenCode 2 has none and relies on
the fresh container, see [the coding agents](#the-coding-agents)), so the agent
carries no implicit session memory between iterations. What it knows of the past is only what `/refs/`
deliberately shows it. Note this is not a hermetic boundary: `/workspace` is
writable, the network is open, and for Codex the host's `~/.codex` is bind-mounted
read-write — statelessness there rests on those flags, not on the container.

### The information diet

The mutator image is built **from the same NLE base as the arena**, so the agent
experiments against the same compiled NLE it will be scored on — parity for free,
as long as both images are built from the same base **and the same CPU
architecture**. The mutator stays multi-arch and runs natively — it drives the
coding agent, not scoring — while the arena pins `linux/amd64` and emulates
elsewhere (more under Safety and sandboxing, below). On an amd64 host the two
line up. On Apple Silicon or arm64 Linux they don't: the mutator's embedded NLE
is then compiled for a different architecture than the one your bot is actually
scored on. Base drift is the other failure mode. The pins record the base the
published pair share, and in a checkout the mutator builds on that pinned base,
so drift there is bounded; the arena no longer builds locally at all.

The `nethackers` package is deliberately *not* installed in it. Only
`nethackers.arena` and `nethackers.contracts` are copied onto `PYTHONPATH` — enough
to score a candidate locally, and `import nethackers.harness.seeds` raises
`ModuleNotFoundError` inside the container. See
[`Dockerfile.mutator`](../Dockerfile.mutator).

**Be precise about what that does and does not achieve**, because it is easy to
oversell:

- It is an information **diet**, not a security boundary. The container has open
  network egress and the package is on PyPI, so an agent that wanted
  `harness.seeds` back could `pip install nethackers`. It shapes what the agent
  reaches for by default; it does not prevent anything.
- It does **not** withhold the seed derivation. `nethackers.arena.seeds` — which
  the mutator does have — contains the full HMAC formula, and the public secret is
  the literal string `"public"`. The agent can reproduce every public game exactly.
  That is fine: they are published, and letting the agent evaluate its own
  candidates is the whole point of giving it a live NLE.
- What actually protects the hidden seeds is that the **hidden secret is not in
  any image**. It lives only in the hub's and verifier's environment. No amount of
  access inside the mutator recovers it.

So the honest framing: `harness/` is withheld to keep the agent's attention on the
bot rather than the scoring machinery, and the hidden tier is safe because of the
secret, not because of the image layout.

---

## 3. Safety and sandboxing

> **This section is the part most worth reusing** — including the parts where we
> tell you our own boundary is thinner than it looks. An audit of this document
> against the code (2026-09-08) found several places where an earlier draft
> claimed protection the implementation does not provide. What follows is the
> corrected version.

### The threat model, stated up front

**Accident-grade, and scoped to the mutator.** The design this inherits was
written for one surface: our own model's generated code, on our own machines. It
defends against *thrashing and blast radius* — fork bombs, runaway memory, an
agent that `rm -rf`s outside its worktree. It explicitly does **not** defend
against a determined adversary. That ruled out microVMs and gVisor: isolation we
didn't need at overhead we'd pay.

Two honest consequences of that scoping, which this document previously glossed:

- **The bot-evaluation surface inherits the threat model without inheriting the
  controls.** It is also the one surface that runs *strangers'* code, where
  "our own model, on our own machine" stops being true.
- **Credentials are in reach** on the mutator surface, so "no secrets in reach"
  is not accurate there either.

If you are evaluating code from people you don't trust, on a machine that
matters, **this design is not sufficient for you** — and that includes anyone
running a public verifier on the same code path.

### Three surfaces that execute untrusted code

#### a) The bot being evaluated

Every eval imports and runs a `bot.py` you may not have written.

What [`eval/runner.py`](../src/nethackers/eval/runner.py) does:

```
<docker|podman> run --rm --network none \
  -e NETHACK_ARENA_SECRET=... \
  -v <solution>:/sol:ro \
  -v <tmpdir>:/out \
  ghcr.io/dunnolab/nethackers-arena@sha256:...
```

- **`--network none`** — no egress. This one is real and it is the strongest
  control on this surface.
- **`/sol:ro`** — the solution is mounted read-only.
- **A fresh host temp dir at `/out`**; the host reads only `results.json` back.

Inside the container, [`arena/sandbox.py`](../src/nethackers/arena/sandbox.py)
runs the bot in its own subprocess, which pops `NETHACK_ARENA_SECRET` from its
environment, clears the write flag on observation arrays, and enforces per-action
timeouts (`BotTimeout`).

**Now the limits, stated plainly, because the above reads stronger than it is:**

- **A bot can influence its own score.** The arena puts the solution directory at
  the *front* of `sys.path` so `import bot` resolves, and it imports NLE lazily
  afterwards. A solution that ships modules named like the ones the scorer imports
  is therefore importable *by the scorer*, in-process. `:ro` does not help — this
  is import, which only reads. **Treat every self-reported score as an unaudited
  claim; that is exactly what the Public/Private tier split is for.**
- **There are no resource limits on this surface at all.** No `--pids-limit`, no
  `--memory`, no `--cpus`, no wall-clock `timeout`. The cgroup caps described
  under (b) are on the *agent* container only. A fork bomb in a bot is a fork bomb
  in your container, and `BotTimeout` kills only the direct child — grandchildren
  survive until the container exits.
- **The container runs as root** (no `USER` in the arena image), so in-container
  isolation between the bot and the scorer is fault isolation, not a trust
  boundary. The secret and the batch are readable from `/proc` regardless of the
  environment pop.
- **The digest pin is a default, not a guarantee.** An explicit `--image`,
  `NETHACKERS_ARENA_IMAGE`, or a repo checkout's `.env.stack` still wins over
  the pin, so a local `eval` can be pointed at unpinned bytes — including a
  locally built tag, which only arena *development* needs. What changed: a
  repo checkout no longer defaults to the mutable `nethackers/arena:dev` on
  its own; the pin wins there too now, same as everywhere else. Evidence
  records the digest it resolved, and the hub now refuses to register
  anything whose digest it can't classify at the current arena major — a
  locally built tag is unclassified by construction, so it can produce a
  local score but never a submittable one.
- **`linux/amd64` is the reference architecture; other hosts emulate.**
  NetHack's C code consumes RNG in sibling function arguments, and gcc
  sequences those differently per CPU target, so the same seed plays a
  different game on `arm64` than on `amd64` — a native arm64 score isn't
  comparable, which is why the hub refuses it outright rather than just
  discouraging it. On Apple Silicon, turn on Rosetta in Docker Desktop
  (Settings → General → Apple Virtualization framework → "Use Rosetta for
  x86_64/amd64 emulation"): the same 15-episode batch on the same machine
  took 823s under QEMU and 224s with Rosetta. `nethackers doctor` reports
  whether it's on.

#### b) The coding agent

`nethackers evolve` runs the agent with permission prompts **fully disabled**,
because it runs unattended for hours and cannot stop to ask. That is only
defensible because the CLI runs inside a container
([`container_operator.py`](../src/nethackers/harness/container_operator.py)):

```
docker run --rm \
  --pids-limit 512 \
  --memory 8g --memory-swap 8g \
  --cpus 4 \
  --security-opt no-new-privileges \
  -v <worktree>:/workspace \
  -v <refs>:/refs:ro \
  <mutator-image> timeout 28800 <agent CLI ...>
```

- **`--pids-limit`** is the fork-bomb defense, and the reason this is a container
  rather than a process wrapper: no process-level sandbox — Seatbelt, bubblewrap,
  Landlock, or the agent CLIs' own — imposes CPU/memory/pid caps. That defense
  lives only in cgroups.
- **`--memory-swap` pinned to `--memory`**, else a runaway reaches ~2× by swapping.
- **`no-new-privileges`**, and the entrypoint drops to a non-root `agent` user
  (it starts as root to remap uids, then `gosu`-drops).
- **`timeout 28800`** (8h) sends SIGTERM with no `--kill-after`, so it is a
  ceiling only insofar as the agent CLI honors SIGTERM.

**What this does not contain:**

- **Open network egress.** The agent CLIs need their model APIs, so there is no
  restriction at all. Egress allow-listing is designed but not implemented. This
  is the largest hole in the sandbox.
- **`/workspace` is not the only writable mount.** For **Codex**, the host's real
  `~/.codex` is bind-mounted **read-write**. Codex reads its own config from
  there on the host, so code running in the cage can influence what your *next
  host-side* `codex` run does. For Claude on Linux only a read-only credentials
  file is mounted; on macOS the OAuth token is passed as an environment variable
  (visible in `docker inspect`). For OpenCode 2 a read-only copy of your provider
  definitions is mounted, which may hold a literal key, and the keys it names
  arrive as environment variables (also visible in `docker inspect`). An agent
  that wanted to exfiltrate your coding-agent credentials could — and for Codex,
  modify them.
- **Blast radius is not confined to the worktree.** Host-side steps after the run
  (`copytree` into the tree store, into the next `/refs`, and into the published
  repo) follow symlinks by default. Combined with register-all publishing, a
  symlink planted in the worktree pointing at a host-readable file can end up in a
  public commit. We have not seen this happen; it follows from the code.

#### c) Programs you pull

`nethackers pull` clones a repo; nothing executes at clone time. The tree is then
on disk, and the next `eval` runs it under (a).

Note the CLI does **not** enforce that you pinned a commit — `pull owner/name@main`
resolves a branch happily. The *hub* validates registered references as 40-hex
commits that exist, so anything you fetch by way of a board row is pinned; a
hand-typed ref is whatever you typed.

### If you are building your own harness

1. **Put resource caps on every container that runs code you didn't write** —
   `--pids-limit`, `--memory` + `--memory-swap`, `--cpus`, wall-clock `timeout`.
   Note we do this on the agent and *not* on the evaluator; do not copy that.
2. **Run bot evaluation with `--network none`.** Cheap, and removes a whole class
   of problem.
3. **Don't put untrusted code on the interpreter's `sys.path` inside your
   scorer.** Load it out-of-process with the scorer's own modules resolved first,
   or accept that a submission can reach your scoring logic. This is the mistake
   worth not repeating.
4. **Run the evaluator as a non-root user**, so in-container separation means
   something.
5. **Treat self-reported numbers as claims.** The literature on self-improving
   systems is a catalogue of harness exploits and scorer bugs; an independent
   re-run on seeds the author never saw is the only score worth ranking on.
6. **Copy without following symlinks** (`copytree(..., symlinks=True)`) anywhere
   you move an agent's output around, especially before publishing it.
7. **Be explicit about what you don't defend against**, and re-check it when you
   add a surface — this section was wrong until it was audited against the code.


## 4. Building your own harness

Any search is fair. The platform scores what comes out, so:

- **Conform to the contract** — `bot.py`, `make_agent()`, integer actions.
- **Score locally** with `nethackers eval` against a catalog objective. It
  runs the pinned `linux/amd64` arena image by default — that pin, not a
  self-built one, is what evidence for the hub has to come from. You can
  still build the arena image yourself for arena *development*, but a
  self-built image is an unclassified tag, and the hub refuses evidence
  from it.
- **Register a `repo@commit`** with `nethackers submit` (which publishes to your
  own `github.com/<you>/nethacker` and registers it), or — if you already pushed
  it somewhere public — `nethackers register --repo github.com/you/name --commit
  <sha> --evidence eval.json`, where the evidence JSON is what `nethackers eval`
  produced.
- **Start from an elite** if you want. `nethackers elites --scope <identity>`
  tells you the current best; `nethackers pull` fetches it. Building on someone
  else's program is the intended path, not a shortcut.

Your search is never metered, never sandboxed by us, and never inspected. It can
run on a cluster, in a notebook, or in your head. What lands on the board is the
program.

### What to take from ours, and what to leave

| Worth borrowing | Probably skip |
|---|---|
| The resource caps on the agent ([§3](#b-the-coding-agent)) | Multi-arch image builds — you know your own machine |
| `--network none` + read-only mount for scoring | Three coding-agent backends, model discovery, `doctor` |
| Sealing the agent from its own past | The TUI, auto-provisioning, run monitoring |
| Keeping the hidden secret out of every image | MAP-Elites specifically — any archive shape works |
| A live env the agent can score candidates in | Our cell definition (one per identity) |

The right-hand column exists because this harness has to work for strangers. Yours
doesn't.

Two things worth knowing:

- **The two tiers do different jobs.** Develop against the public seeds — that is
  what they are for, and ours does exactly that with no held-out gate of its own.
  Whether an improvement transfers is answered separately, by the Private Dungeons
  tier ([`verification.md`](verification.md)), on seeds nobody can tune against.
  You are welcome to hold out your own seeds too, but you are not expected to.
- **Determinism is a feature you should protect.** A bot that reads the clock,
  the network, or unseeded RNG is not replayable, which is the property the whole
  platform rests on. The arena seeds `random` and `numpy.random` per episode for
  you; don't route around it.

---

## See also

- [`../README.md`](../README.md) — install, quickstart, Public/Private Dungeons
- [`verification.md`](verification.md) — the verified tier and its verifier
- [`troubleshooting.md`](troubleshooting.md) — when the container won't start
- [`local-stack.md`](local-stack.md) — running a full local hub to develop against
