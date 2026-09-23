# Troubleshooting

What a command printed, what it means, what to do. Reviewed at v0.36.2.

```bash
nethackers setup      # fixes what it can, prints the rest
nethackers doctor     # changes nothing: what this machine can do, and why not
```

Every heading below is the text the terminal printed, a doctor row, or the
symptom when nothing was printed. Doctor rows are quoted in their
`nethackers doctor -o plain` form; on a terminal `[OK]` is `✓` and `[FAIL]`
is `✗`. For a crash, `nethackers report` prints the most recent local crash
report (`~/.nethackers/evolve/crashes/`); nothing is ever sent anywhere.
`NETHACKERS_DEBUG=1` re-raises the `nethackers: unexpected error` line with
its traceback. The other one-line errors below never print one.

## setup and doctor

### sandbox unavailable: no working container runtime found

```text
sandbox unavailable: no working container runtime found — run `nethackers setup --for eval` to set one up, then retry
```

Doctor shows the same as `[WARN] docker: not installed` and
`[WARN] podman: not installed` under `container_runtime`.

**Cause** No container runtime, or Podman aliased as `docker` in your
shell: an alias never reaches a subprocess.
**Fix** `nethackers setup`. On a Mac with Homebrew it installs Colima and
starts it with Rosetta; a Mac without Rosetta 2 is told to install it first
(`softwareupdate --install-rosetta --agree-to-license`) and gets the VM on
the next run. On Linux it prints the install commands, which need `sudo`.
If you use Podman, install it as `podman` and drop the alias.
**Verify** `nethackers doctor -o plain` shows
`[OK] container_runtime: docker is available` (or `podman is available`).

### docker is installed but the runtime check fails

```text
[FAIL] container_runtime: docker: Cannot connect to the Docker daemon …
```

**Cause** Doctor tells "not installed" from "installed but broken" and
prints the runtime's own last line. It is usually a daemon that is not
running, or a user without permission on the socket.
**Fix** Start the runtime: `colima start`, Docker Desktop, `orb start`, or
on Linux `sudo systemctl enable --now docker`. For a permission error on
Linux, add yourself to the `docker` group and log out and back in, or
`newgrp docker` in that shell. Restarting a daemon that is already up does
nothing; read the line.
**Verify** `nethackers doctor -o plain` shows
`[OK] container_runtime: docker is available`.

### amd64 evaluation is running under QEMU, not Rosetta

Doctor's `rosetta` row says one of `amd64 evaluation is running under QEMU,
not Rosetta`, `Colima runs amd64 under QEMU, not Rosetta`, `Podman runs
amd64 under QEMU on Apple Silicon`, or `Rosetta isn't installed on this
Mac, so amd64 runs under QEMU`.

**Cause** On Apple Silicon the `linux/amd64` images run under QEMU unless
the runtime uses Rosetta. On one machine the same 15-episode batch took
823 s under QEMU and 224 s with Rosetta.
**Fix** Docker Desktop: Settings → General, turn on both "Apple
Virtualization framework" and "Use Rosetta for x86_64/amd64 emulation" (it
restarts). Colima: a VM created without Rosetta has to be recreated,
`colima delete` (this deletes its images and containers), then
`nethackers setup`. OrbStack always uses Rosetta. Podman stays on QEMU. If
Rosetta 2 itself is missing: `softwareupdate --install-rosetta
--agree-to-license`.
**Verify** the `rosetta` row of `nethackers doctor` says Rosetta is on:
`Rosetta is accelerating amd64 emulation` (Docker Desktop), `Colima runs
amd64 with Rosetta`, or `OrbStack runs amd64 with Rosetta`.

### unreachable — ghcr.io/dunnolab/nethackers-arena@sha256:…

**Cause** The image is neither on this machine nor pullable: no network,
no route to the registry, or no container runtime yet to ask with (fix that
row first). A fresh install with a network shows
`not local yet, but pullable — <ref> -> run nethackers setup` instead,
which is not a problem.
**Fix** Fix network or registry access, then `nethackers setup` pulls it.
`NETHACKERS_ARENA_IMAGE` can point at another ref, but the hub refuses a
score from any other image, and the CLI shows that only as `hub error: 400`.
**Verify** `nethackers doctor -o plain` shows
`[OK] arena_image: present — ghcr.io/dunnolab/nethackers-arena@sha256:…`.

### stale ghcr login — run `docker logout ghcr.io`, then retry

**Cause** A pull of a sandbox image failed on a stored registry login the
image does not need.
**Fix** The command in the message. Its siblings: `couldn't reach the
registry — only the first run needs the network; a pulled image keeps
working, so retry once you're online`, and, in a checkout only,
`no published sandbox for this build — clone the repo, or set
NETHACKERS_ARENA_IMAGE`.

### a step is marked (untested)

**Cause** That recipe was written from the vendor's documentation and
nobody has run `nethackers setup` through it on a real machine yet
([setup.md](setup.md#what-has-been-run-on-real-machines)).
**Fix** Setup shows the exact command before running it; run it. If it
works, or doesn't, open an issue saying so and on which machine; a PR that
records it flips the row.

## eval

### setting up the arena sandbox (first run — this can take a few minutes)…

**Cause** `evolve` prints this, and `eval` shows a `pulling arena` bar with
the size, speed and time left, while the arena image downloads: about half
a gigabyte, pinned by digest. `evolve` also pulls the mutator, about
900 MB. Under Podman the bar shows layers, not bytes.
**Fix** Wait, or pull ahead of time with `nethackers setup`, whose plan
shows the download as `up to N MB`.
**Verify** `nethackers doctor -o plain` shows
`[OK] arena_image: present — …`.

### sandbox platform mismatch

```text
sandbox platform mismatch — the mutator image runs linux/arm64 but the arena image runs
linux/amd64, so the coding agent would test its changes on different NetHack games than
the ones they are scored on. <fix>
```

The fix names one of three things: `Remove the mutator image so the next
run fetches it again: docker image rm <ref>` for the pinned or a
fingerprint-tagged image, `drop its image override` for a digest given by
`--image`, `--mutator-image` or `NETHACKERS_*_IMAGE`, or `Rebuild the
mutator image for linux/amd64 with make mutator MUTATOR_IMAGE=<ref>` for
any other tag.

**Cause** One image on this machine was built for the host's own
architecture: an older `make mutator` on Apple Silicon, or an override.
The same seed plays a different game on another architecture, and only
`linux/amd64` games are scored.
**Fix** Do what the message names.
**Verify** `docker image inspect <ref> --format '{{.Os}}/{{.Architecture}}'`
prints `linux/amd64` for both images.

## evolve

### not logged in: run `claude auth login` on this host, then retry

Doctor shows the same as `[FAIL] claude: not logged in` under `operator`
(`codex` and `opencode2` likewise).

**Cause** `evolve` needs one coding agent logged in on this machine; the
sandbox reuses that login.
**Fix** `claude auth login`, or `codex login`, or configure OpenCode's
providers ([setup.md](setup.md#coding-agents)); `nethackers setup
--operator <name>` walks through it.
**Verify** the agent's row under `operator` in `nethackers doctor` says
`logged in` (OpenCode without a key says `free models only`, which counts).

### ✗✗ aborting — the operator refused the request

```text
iter 1/5 · ✗ operator error: claude operator exited with status 1: unrecognized model 'claude-opus-5-5' — the sandbox's CLI doesn't know that id; use an alias like `opus`, or update the CLI in the mutator image
iter 1/5 · ✗✗ aborting — the operator refused the request: claude operator exited with status 1: …
```

**Cause** `--model` was checked against your account's model list, which
the picker and the preflight read, but the Claude CLI pinned inside the
sandbox resolves the id against its own list and does not know it. Since
v0.36.2 one such refusal ends the run instead of costing three iterations.
The monitor's now-line reads `Stopped after 1 failed agent runs in a row`.
Codex and OpenCode rejections are ordinary failures and still take three
tries: `✗✗ aborting — 3 consecutive operator failures: <detail>`.
**Fix** `nethackers models --operator claude` lists what the sandbox serves;
pick one of those, or an alias like `opus`, which never goes stale. A newer
model needs a newer mutator image, which arrives with a nethackers release.
**Verify** the run passes iteration 1.

### the loop runs but nothing ever registers

**Cause** In order of frequency. The run started with `not logged in —
running offline (publishing needs nethackers login)` or `wins won't publish
— run gh auth login`. The login expired and its refresh failed, which
degrades registration to `local-only` instead of failing the run. Something
on your network re-signs HTTPS (next entry). Or the loop is working and the
mutations are not wins: the frontier is hard to move
([harness.md](harness.md#not-in-this-loop)). Every scored program is
registered, win or not, so a run with no `local-only` lines did reach the
hub.
**Fix** Read `hub_reason` in the run's `metrics.jsonl`
(`~/.nethackers/evolve/runs/<id>/`, `runs/latest` for the newest).
`local-only: not published`: `gh auth login`, or `nethackers login`.
`local-only: auth failed … hub token expired and could not refresh`:
`nethackers login` again. `local-only: hub error — …`: the next entry, or a
400 from the hub (below). Otherwise nothing is wrong.
**Verify** `metrics.jsonl` says `"outcome": "registered"`, or the monitor's
iteration row says `sent to the hub`.

### fewer episodes run in parallel than the machine has cores

**Cause** The default is one episode per CPU the container runtime has,
within three quarters of its memory at about 1 GiB per episode, never more
than the batch, and 8 when the runtime does not report its resources. On a
Mac those are the CPUs and memory given to the runtime (Docker Desktop:
Settings → Resources), not the Mac's own.
**Fix** `--max-parallel-evals N`, or give the runtime more resources. More
than the machine has is not faster: the arena's per-action timeout is
wall-clock, so contention lowers scores, and an episode that runs out of
memory scores 0. Asking for more than the box holds runs anyway and prints
`arena · warning: N episodes at once are budgeted X GiB, but the arena may
use Y of this machine's Z GiB; an episode that runs out of memory scores 0
-- lower --max-parallel-evals`.

## login and submit

### can't reach GitHub to sign in

```text
can't reach GitHub to sign in (https://github.com/login/device/code) — check your network connection or DNS
```

**Cause** `nethackers login` talks to github.com, not to the hub. The same
line appears when another command refreshes an expired login.
**Fix** Network or DNS, on your side.
**Verify** `nethackers login` completes; `nethackers whoami` shows you.

### cannot reach the hub

```text
cannot reach the hub (<url>) — is it running? (docker compose up -d)
```

`whoami` and `doctor` say `hub unreachable at <url>` instead.

**Cause** The CLI is pointed at a hub it cannot reach. Inside a worktree,
`.env.stack` points at that worktree's local hub, announced on stderr as
`stage: <name> · hub <url>` on every invocation except `--version`; `--hub`
and `$NETHACKERS_HUB` override explicitly.
**Fix** `nethackers whoami` shows the active stage and URL. For production,
`nethackers --prod`; for the local hub, `make hub`
([local-stack.md](local-stack.md)). If the URL is right, see the next entry.
**Verify** `nethackers whoami` no longer says `hub unreachable at`.

### cannot reach the hub, on a network that inspects HTTPS

**Cause** A firewall or antivirus re-signs every HTTPS certificate with its
own CA. The CLI's own line only says `cannot reach the hub`; the reason,
`CERTIFICATE_VERIFY_FAILED`, shows up as `hub_reason` in a run's
`metrics.jsonl`. nethackers trusts the OS certificate store, so the fix is
getting that CA into it.
**Fix** On Debian and Ubuntu the system bundle via `update-ca-certificates`,
on Fedora and RHEL via `update-ca-trust`; on macOS the System keychain. Or
ask IT to exempt `nethackers.dunnolab.ai`.
**Verify** `nethackers whoami` reaches the hub.

### invalid hub URL

```text
invalid hub URL: <error> — include a scheme, e.g. --hub http://localhost:8000
```

**Fix** Include the scheme: `--hub http://localhost:8000`, not
`--hub localhost:8000`.

### gh is authed as one account but you're logged in as another

```text
gh is authed as @<a> but you're logged in as @<b> — sign in to the same account
(`nethackers setup --for publish` checks this)
```

**Cause** `nethackers login` and `gh auth login` are separate logins, and
`submit` needs both as the same account: `gh` creates and pushes the
repository, the hub attributes the registration to your login.
**Fix** `gh auth status` and `nethackers whoami` say who each thinks you
are; `gh auth switch` if gh already knows the other account, else sign one
of them in to the other's account.
**Verify** `nethackers setup --for publish` passes the same-account check.

### gh not authed

`submit` prints this; `evolve` starts with `wins won't publish — run gh auth
login (separate from nethackers login)`; doctor says `gh is installed but
not logged in`.

**Fix** `gh auth login`, separate from `nethackers login`; or
`nethackers setup --for publish`.

### hub token expired and could not refresh — run `nethackers login`

**Fix** The command in the message. It appears on `register`, `submit` and
at `evolve` start when the stored login is past its refresh.

### hub error: 400 for <hub>/register

**Cause** The hub refused the registration and the CLI shows only the
status; a run records `local-only: hub error — Client error '400 Bad
Request'`. In the hub's order, a 400 means: not a github.com repository;
the commit does not exist on GitHub as your token sees it (unpushed,
force-pushed away, or a private repository of someone else); an unknown
objective; the wrong batch of seeds; a non-finite score; no arena image in
the evidence; an image the hub has not classified (a local build or an
override); or an evidence image from an older arena major, whose message
says `upgrade the nethackers CLI`. A 403 means your login does not own the
repository; a 502 means the hub could not reach GitHub.
**Fix** Push first, then register the pushed sha; drop any image override;
upgrade nethackers.
**Verify** `nethackers search --owner <you>` lists it, and
`nethackers show <id>` opens it.

## install

### command not found: nethackers

**Cause** `pip install` put the console script somewhere off your `PATH`.
There is no `python -m nethackers`; the script is the only entry point.
**Fix** Add that environment's `bin` (or `Scripts`) directory to `PATH`, or
`uv tool install nethackers`, which manages the shim.
**Verify** `nethackers --version`.

## Still stuck

Open an issue at
[github.com/dunnolab/nethackers/issues](https://github.com/dunnolab/nethackers/issues)
with the heading you tried, the complete output, and:

```bash
nethackers --version -o json     # version, run schema, pinned image digests
nethackers doctor -o json        # the environment
nethackers report                # the last crash, if there was one
```
