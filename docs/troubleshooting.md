# Troubleshooting

What a command printed, what it means, what to do. Reviewed at v0.35.0.

```bash
nethackers setup      # fixes what it can, prints the rest
nethackers doctor     # changes nothing: what this machine can do, and why not
```

For a full traceback instead of the one-line error, `NETHACKERS_DEBUG=1`.
For a crash, `nethackers report` prints the most recent local crash report;
nothing is ever sent anywhere. Every heading below is the text the terminal
printed, or the symptom when nothing was printed.

## setup and doctor

### no docker or podman found on PATH

**Cause** No container runtime, or Podman aliased as `docker` in your
shell: an alias never reaches a subprocess.
**Fix** `nethackers setup`. On a Mac with Homebrew it installs Colima and
starts it with Rosetta; on Linux it prints the install commands, which need
`sudo`. If you use Podman, install it as `podman` and drop the alias.
**Verify** `nethackers doctor` shows `[OK] container_runtime`.

### docker is installed but the runtime check fails

**Cause** Doctor tells "not installed" from "installed but broken" and
prints the runtime's own error. It is usually a daemon that is not running,
or a user without permission on the socket.
**Fix** Start the runtime (`colima start`, Docker Desktop, `orb start`), or
on Linux add yourself to the `docker` group and log out and back in.
Restarting a daemon that is already up does nothing; read the error.
**Verify** `nethackers doctor` shows `[OK] container_runtime`.

### Rosetta is not accelerating amd64 emulation

**Cause** On Apple Silicon the `linux/amd64` images run under QEMU unless
the runtime uses Rosetta. The same batch took 823 s under QEMU and 224 s
with Rosetta.
**Fix** Docker Desktop: Settings → General → "Use Rosetta for x86_64/amd64
emulation" (it restarts). Colima: a VM created without Rosetta has to be
recreated, `colima delete` (this deletes its images and containers), then
`nethackers setup`. OrbStack always uses Rosetta. Podman stays on QEMU.
**Verify** the `rosetta` row of `nethackers doctor` says
`Rosetta is accelerating amd64 emulation`.

### unreachable — ghcr.io/dunnolab/nethackers-arena@sha256:…

**Cause** The image is neither on this machine nor pullable: no network, or
no route to the registry.
**Fix** Fix network or registry access, then `nethackers setup` pulls it.
`NETHACKERS_ARENA_IMAGE` can point at another ref, but a score from an
unpinned image can be computed and never registered.
**Verify** `nethackers doctor` shows `[OK] arena_image: present`.

### a step is marked (untested)

**Cause** That recipe was written from the vendor's documentation and
nobody has run `nethackers setup` through it on a real machine yet
([setup.md](setup.md#what-has-been-run-on-real-machines)).
**Fix** Setup shows the exact command before running it; run it. If it
works, or doesn't, an issue saying so on which machine flips the row.

## eval

### the first eval sits for minutes before a game starts

**Cause** It is pulling the arena image, about 1 GB, pinned by digest.
**Fix** Wait, or pull ahead of time with `nethackers setup`, which shows a
progress bar and the time left.
**Verify** `nethackers doctor` shows `[OK] arena_image: present`.

### sandbox platform mismatch

```text
sandbox platform mismatch — the mutator image runs linux/arm64 but the arena image runs
linux/amd64, so the coding agent would test its changes on different NetHack games than
the ones they are scored on. Rebuild the mutator image for linux/amd64 with `make mutator
MUTATOR_IMAGE=<ref>`, or drop its image override
```

**Cause** One image on this machine was built for the host's own
architecture: an older `make mutator` on Apple Silicon, or an `--image` /
`--mutator-image` override. The same seed plays a different game on another
architecture, and only `linux/amd64` games are scored.
**Fix** Run the rebuild the message names, or drop the override
(`--image`, `--mutator-image`, `NETHACKERS_ARENA_IMAGE`,
`NETHACKERS_MUTATOR_IMAGE`).
**Verify** `docker image inspect <ref> --format '{{.Os}}/{{.Architecture}}'`
prints `linux/amd64` for both images.

## evolve

### operator: not logged in

**Cause** `evolve` needs one coding agent logged in on this machine; the
sandbox reuses that login. Doctor lists each agent's state under
`operator`.
**Fix** Run `claude` once, or `codex login`, or configure OpenCode's
providers ([setup.md](setup.md#coding-agents)); `nethackers setup
--operator <name>` walks through it.
**Verify** the `operator` row of `nethackers doctor` shows `logged in`.

### the loop runs but nothing ever registers

**Cause** Three possibilities, in order of frequency. The login expired and
its refresh failed, which degrades registration to `local-only` instead of
failing the run. Something on your network re-signs HTTPS (next entry). Or
the loop is working and the mutations are not wins: the frontier is hard to
move ([harness.md](harness.md#not-in-this-loop)).
**Fix** Read `hub_reason` in `~/.nethackers/evolve/runs/<id>/metrics.jsonl`.
`local-only`: `nethackers login` again. `CERTIFICATE_VERIFY_FAILED`: the
next entry. Otherwise nothing is wrong.
**Verify** the run log says `registered`.

### fewer episodes run in parallel than the machine has cores

**Cause** The default is one episode per CPU the container runtime has,
within three quarters of its memory at about 1 GiB per episode, and never
more than the batch. On a Mac those are the CPUs and memory given to the
runtime (Docker Desktop: Settings → Resources), not the Mac's own.
**Fix** `--max-parallel-evals N`, or give the runtime more resources.
More than the machine has is not faster: the arena's per-action timeout is
wall-clock, so contention lowers scores, and an episode that runs out of
memory scores 0 (asking for that prints a warning).

## login and submit

### can't reach GitHub to sign in

```text
can't reach GitHub to sign in (<error>) — check your network connection or DNS
```

**Cause** `nethackers login` talks to github.com, not to the hub.
**Fix** Network or DNS, on your side.
**Verify** `nethackers login` completes; `nethackers whoami` shows you.

### cannot reach the hub

```text
cannot reach the hub (<url>) — is it running? (docker compose up -d)
```

**Cause** The CLI is pointed at a hub it cannot reach. Inside a worktree,
`.env.stack` points at that worktree's local hub, announced on stderr as
`stage: <name> · hub <url>` on every invocation; `--hub` and
`$NETHACKERS_HUB` override explicitly.
**Fix** `nethackers whoami` shows the active stage and URL. For production,
`nethackers --prod`; for the local hub, `make hub`
([local-stack.md](local-stack.md)). If the URL is right, see the next entry.
**Verify** `nethackers whoami` answers.

### CERTIFICATE_VERIFY_FAILED

**Cause** A firewall or antivirus re-signs every HTTPS certificate with its
own CA. It shows up as `hub_reason` in a run's `metrics.jsonl`, or in the
traceback under `NETHACKERS_DEBUG=1`. nethackers trusts the OS certificate
store, so the fix is getting that CA into it.
**Fix** On Linux, the system bundle that `update-ca-certificates` builds;
on macOS, the Keychain. Or ask IT to exempt `nethackers.dunnolab.ai`.
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
are; sign one of them in to the other's account.
**Verify** `nethackers setup --for publish` passes the same-account check.

### gh not authed

**Fix** `gh auth login`, separate from `nethackers login`; or
`nethackers setup --for publish`.

### a registration is refused because the commit is not on GitHub

**Cause** The hub checks that the commit exists in a public repository
before accepting it. Unpushed, force-pushed away, or private, and it is
refused.
**Fix** Push first, then register the pushed sha.
**Verify** `nethackers show <program-id>` finds it.

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
