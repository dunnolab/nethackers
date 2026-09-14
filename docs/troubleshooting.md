# Troubleshooting

```
  _____                _    _         _           _   _
 |_   _| _ ___ _  _ __| |__| |___ ___| |_  ___  _| |_(_)_ _  __ _
   | || '_/ _ \ || / _` / _` / -_|_-<| ' \/ _ \/ _  _| | ' \/ _` |
   |_||_| \___/\_,_\__,_\__,_\___/__/|_||_\___/\__|\__|_|_||_\__, |
                                                             |___/
```

**Start here:**

```bash
nethackers doctor
```

Eight checks, folded into four capabilities — `browse`, `eval`, `evolve`,
`publish`. It tells you which ones this machine can do and prints a concrete fix
for each failure. `-o json` if you want to gate a script on it. It changes nothing
unless you pass `--pull` (which fetches missing sandbox images), but it is **not**
offline: it makes a hub round-trip, runs `gh auth status`, and probes the registry
for any sandbox image you don't already have. `nethackers report` and
`nethackers --version` are the genuinely offline commands.

For a crash: `nethackers report` prints the most recent local crash report.
Nothing is ever sent anywhere — there is no telemetry in this project. Paste it
into an issue yourself if you want help.

For a full traceback instead of the one-line error: `NETHACKERS_DEBUG=1`. It
only affects *unexpected* errors — the recognized one-liners below (auth, GitHub
unreachable, hub unreachable, bad hub URL) are returned deliberately and ignore
it.

---

## Install and environment

### `nethackers` runs but ignores my source changes

Mostly fixed: `[tool.uv] cache-keys` in `pyproject.toml` keys the install cache on
`pyproject.toml` and `src/**/*.py`, so a path install picks up Python edits. It
does **not** key on non-`.py` assets — notably
`src/nethackers/hub/web/index.html` — so an edit to the web page can still be
served stale.

```bash
uv cache clean nethackers        # then reinstall
# or, for development, don't install at all:
uv run nethackers ...            # always live source
```

`make install` does the cache-clean for you.

### `command not found: nethackers`

`pip install nethackers` put the console script somewhere off your `PATH`. There
is no `python -m nethackers` fallback — the package has no `__main__` module, so
the `[project.scripts]` shims are the only entry points. Either add that
environment's `bin`/`Scripts` directory to your `PATH`, or install isolated with
`uv tool install nethackers`, which manages the shim for you.

---

## Containers

### "no docker or podman found on PATH"

Both are supported and either is fine — `docker` wins if you have both.

If you have podman aliased as docker in your shell, that **will not work**:
aliases are a shell construct and never appear on `PATH`, so a subprocess cannot
see them. Install podman properly (it is detected directly, by name) and drop the
alias.

### `docker` is installed but the check still fails

`doctor` distinguishes *not installed* from *installed but broken* and shows you
which. "Broken" carries the actual `info` error — usually the daemon is not
running, or your user lacks socket permission. Restarting a daemon that is
already up will not help; read the per-CLI breakdown.

### Rootless podman: evolve from the TUI fails with a missing `docker`

**Known open issue ([#54](https://github.com/dunnolab/nethackers/issues/54)).**
The CLI threads the detected runtime through correctly; the TUI's Start path does
not, so it launches `docker ...` on a podman-only machine even though its own
readiness strip correctly reports podman.

Workaround — use the CLI for evolve on podman-only machines:

```bash
nethackers evolve <objective> --seed roots/autoascend --operator codex
```

### Rootless podman: mutator fails with `EACCES` on `/workspace`

Same issue, second half, also unresolved. Rootless podman maps your host uid to
container uid 0, so the bind-mounted worktree and the injected agent credentials
appear root-owned to the container's non-root `agent` user.

No fix has shipped. The proposed one is `--userns=keep-id` on the **mutator**
run only — never on the arena run, where it would change the scoring environment.
Note that `keep-id` alone is not enough: the image's entrypoint starts as root to
remap the uid and `gosu`-drop to `agent`, so it needs a non-root entrypoint path
as well. Treat this as a direction, not a recipe.

### The first `eval` takes forever

It is pulling the arena image (several GB), pinned by digest. `nethackers doctor
--pull` fetches the sandbox images ahead of time so the first real run doesn't
stall on it.

### "unreachable — ghcr.io/dunnolab/nethackers-arena@sha256:…"

Neither local nor pullable. The **arena** always resolves to the pinned
digest, even inside a repo checkout, so `eval`/`evolve` never fall back to
building it locally here — fix your network/registry access, or point
`NETHACKERS_ARENA_IMAGE` at a reachable ref. The **mutator** is different: in
a checkout, `make mutator` (which `evolve` also runs automatically) still
builds it locally; outside one, `NETHACKERS_MUTATOR_IMAGE` is the escape
hatch.

---

## Login and publishing

### "can't reach GitHub to sign in"

`nethackers login` talks to **github.com**, not to the hub. This error is a
network or DNS problem on your side — it is deliberately worded to keep you from
chasing a local hub that was never involved.

### "cannot reach the hub … is it running?"

The CLI is pointed at a hub it cannot reach. Check which one:

```bash
nethackers whoami        # reports the active stage and hub URL
```

Inside a worktree, an `.env.stack` file points you at that worktree's local hub.
That is announced, not silent — any non-prod stage prints `stage: <name> · hub
<url>` to stderr on every invocation, so check that line. `nethackers --prod`
forces production; `--hub URL` or `$NETHACKERS_HUB` overrides explicitly.

### "invalid hub URL"

Include the scheme: `--hub http://localhost:8000`, not `--hub localhost:8000`.

### `submit` fails on the GitHub push

`nethackers login` and `gh auth login` are **separate logins**, and `submit`
needs both — as the *same account*. `gh` creates and pushes the repo; the hub
attributes the registration to your `nethackers` identity. If they differ, the
push lands somewhere the registration doesn't point.

```bash
gh auth status           # who gh thinks you are
nethackers whoami        # who the hub thinks you are
```

### A registration is rejected

The hub verifies the commit exists on GitHub before accepting it. A commit that
is unpushed, force-pushed away, or in a private repo will be rejected. Push
first, then register the pushed sha.

---

## Evolve

### The coding agent isn't logged in

`evolve` needs `claude` or `codex` authenticated **on the host** — the
credentials are mounted into the sandbox from your host CLI's own login. `doctor`
lists each registered agent's status; at least one must be logged in.

### `codex` inside the sandbox can't import `nle`

Codex runs commands through `bash -lc` (a login shell), and `/etc/profile` resets
`PATH`, dropping the venv's bin directory — so `python` in there is the system
one, without NLE. The image ships an `/etc/profile.d` drop-in that re-prepends
the venv. Arena evaluation is unaffected: it invokes `python -m` directly and
never goes through a login shell.

### The loop runs but nothing ever registers

Most likely it is working correctly and the mutations aren't wins. Before
debugging the machinery, check:

- **Is your login still good?** An expired access token is refreshed
  automatically; only a missing or failed refresh degrades the run to a
  `local-only` registration reason rather than a hard failure.
- **Are the "wins" real?** Improvements on the 15 fixed public seeds per identity
  can sit inside the noise floor. A stalled loop is often correct behavior — the
  frontier is genuinely hard to move — rather than a bug.
- **Is the parent frozen?** If every iteration mutates the same elite with an
  agent that remembers its last attempt, you get redundant mutations forever.
  See [`harness.md`](harness.md#two-design-choices-worth-stealing).

### Runs are slower than they should be

`--max-parallel-evals` (default 8) caps concurrent episodes. It oversubscribes a
4-CPU box and underuses a 16-core one, so tune it per machine. Raising it is not
automatically faster: oversubscription contends for CPU, and the arena's
per-action timeout is wall-clock, so heavy contention can cut normal actions and
depress the score itself.

---

## Local development

### `make up` built the wrong arena image on the first run

`make up` parses `-include .env.stack`, which the very first run hasn't created
yet, so it builds the shared `nethackers/arena:dev` fallback instead of this
worktree's tag. Run `make stack` by itself first. It self-heals on the second
run.

### The local hub misbehaves after a catalog or schema change

Wipe the database and start clean:

```bash
make hub-reset
```

Use the make target, not a bare `docker compose down -v`. Each worktree's stack
runs under its own `COMPOSE_PROJECT_NAME` (sourced from `.env.stack` inside the
make recipes), so a plain `docker compose` in your shell resolves to a
directory-named project and leaves the real `hubdata` volume untouched.

(The old startup crash from a mismatched objective catalog — a `UNIQUE(name)`
violation — is gone: the `objectives` table was dropped and the catalog now lives
only in memory.)

### CI fails: "image-input paths changed (since &lt;tag&gt;) without re-pinning …"

Something that changes the sandbox images' contents was modified —
`Dockerfile.mutator`, `nle-base/Dockerfile`, `arena/Dockerfile`, `uv.lock`,
`src/nethackers/arena/`, or `src/nethackers/contracts/` — without
`_image_pins.py` changing too.

Check the diff it prints before assuming it was you: the base is the **last `v*`
release tag**, not your PR's base branch, so drift from an earlier
merged-but-unreleased PR fails your PR as well. Read
[`contributing.md`](contributing.md#two-traps-that-cost-real-time) before
re-pinning — it is not a formality, and re-pinning the arena image orphans every
verified score accumulated so far.

### A test passes but clearly isn't testing anything

Check whether you patched an injectable seam. Keyword-default dependencies
(`run=subprocess.run`, `repo_root=...`) bind at **import** time, so
`monkeypatch.setattr` on the module attribute is a silent no-op. Inject
explicitly instead.

---

## Still stuck

Open an issue at
[github.com/dunnolab/nethackers/issues](https://github.com/dunnolab/nethackers/issues)
with:

```bash
nethackers --version -o json     # version, run-schema, pinned image digests
nethackers doctor -o json        # environment
nethackers report                # the last crash, if there was one
```
