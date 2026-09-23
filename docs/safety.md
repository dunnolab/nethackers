# Running code you didn't write

Three commands run other people's code. `eval` imports a `bot.py` that may
be a stranger's. `evolve` runs a coding agent unattended for hours with its
permission prompts off. `pull` puts a stranger's repository on your disk.
This page says what contains each one and what does not. How the loop
around them works is in [harness.md](harness.md).

**What can a bot do to your machine?** Nothing outside its container: no
network, a read-only root, no capabilities, a non-root user, capped memory,
CPU and process count, and a kill at a wall-clock ceiling. What it can do is
influence its own score, because it runs in the same process as the scorer.
A self-reported number is a claim, which is why the private tier exists.

**What can the coding agent reach?** Its worktree, the read-only `/refs`,
the open network, and your coding-agent credentials: Codex's `~/.codex`
read-write, Claude Code's token read-only or as an environment variable,
OpenCode's provider keys as environment variables. It cannot fork-bomb the
machine or exhaust its memory, and it runs as a non-root user. Files it
writes are copied host-side after the run, following symlinks, into the next
`/refs` and into your published repository.

**What does a self-reported score prove?** That the author's machine
produced that number on the public seeds. The verifier's number on secret
seeds is the one that survives a bot written to game the public ones
([verification.md](verification.md)).

## Built to stop accidents, not attackers

The controls contain a fork bomb, a memory runaway, an agent that deletes
outside its worktree, a bot that phones home. They do not stop a kernel
exploit; there is no microVM or gVisor here. Running strangers' programs on a
machine that matters, or a public verifier, means treating the container as
a limit on the blast radius, not as a wall.

## The bot

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
  happens inside the box.
- The digest pin is a default. `--image`, `NETHACKERS_ARENA_IMAGE`, or a
  checkout's `.env.stack` still win, so a local eval can run unpinned bytes.
  Evidence records the digest it ran, and the hub refuses to register
  anything it cannot classify at the current arena major, so an unpinned
  image can produce a local score but never a submittable one.

## The coding agent

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

## Programs you pull

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

## Known limitations

- Scores can be self-influenced through the in-process import. Rank on the
  private tier.
- The agent container has open egress and reachable credentials. Don't run
  `evolve` with a credential you cannot rotate.
- Copies after a run follow symlinks. Look at a published tree before you
  point anyone at it.
- The mutator keeps `CAP_SETUID` and `CAP_SETGID` at startup for the uid
  remap.
- `linux/amd64` is the reference architecture. Other hosts emulate it, and a
  native arm64 score is a different game, which the hub refuses. On Apple
  Silicon, Rosetta makes emulation fast: the same 15-episode batch took
  823 s under QEMU and 224 s with Rosetta. `nethackers doctor` says whether
  it is on.

## If you build your own

1. Cap every container that runs code you didn't write: processes, memory
   plus swap, CPU, and a wall clock.
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
