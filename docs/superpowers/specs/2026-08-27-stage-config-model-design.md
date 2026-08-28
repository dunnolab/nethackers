# Stage Config Model & the Local Full Loop

**Status:** design / awaiting review
**Date:** 2026-08-27
**Author:** vkurenkov (with Claude)
**Builds on:** the stage-config research report (`docs/superpowers/2026-08-27-stage-config-research.md` — 24-item constant inventory, coupling map, cited best-practices survey) and buro's per-worktree stack allocator (`/Users/vokneruk/Projects/buro/scripts/worktree_stack.py`).

Throughout, **`<slug>`** is the sanitized worktree basename (`tripletail` here) and **K1–K7 / surfaces 1–5** are the research report's writer/reader couplings and parallel-worktree collision surfaces.

## Problem

The end goal is to **run the whole system locally**: a per-worktree stack, the full evolve loop with a real operator, publishing to real GitHub, registering, boards. Today that path is blocked twice over — by config, and by auth.

**Config.** The environment/"stage" configuration exists, but only as ~18 hardcoded literals scattered across five layers, with no first-class model. Every value that should differ between prod, a local stack, and a sibling worktree is a copy-paste constant:

- **Hub URL** — two contradictory defaults: `cli._default_hub()` says `https://nethackers.dunnolab.ai` (`cli.py:109`), `EvolveParams.hub` says `http://localhost:8000` (`harness/launch.py:63`). Which one you get depends on which constructor you reach through (**K7** — the past "evolve silently registered against the wrong hub" run-integrity bugs). A third copy lives in stale help text: `_common_parser` claims the top-level default is `http://localhost:8000` (`cli.py:197-198`) while `_default_hub()` returns prod.
- **Hub port** — `compose.yaml:13` honors `${NETHACKERS_HUB_PORT:-8000}`, but the Makefile's `wait-hub` curls a hardcoded `:8000` (`Makefile:65`, echo at `:56`), so the override is **already broken**: `NETHACKERS_HUB_PORT=9000 make up` starts a hub on 9000 and waits on 8000 forever (**K2**).
- **Runs dir** — the harness writes `<workdir>/runs` (`cli.py:331` → `launch.py:131`); the TUI reads a hardcoded `~/.nethackers/evolve/runs` twice (`tui/screens/home.py:34`, `tui/screens/runs.py:21`). `--workdir X` makes runs invisible to the TUI (**K1**).
- **Dev identity** — `"dev-token"`/`"dev"` in three source files (`cli.py:544-545`, `launch.py:64-65`, `tui/screens/evolve_form.py:326-327`) must match compose's stub map `{"dev-token":"dev"}` (`compose.yaml:18`) or local registration 401s (**K3**).
- **Image tags** — `nethackers/arena:dev` ×5 (`cli.py:269,321,414`, `launch.py:62`, `Makefile:7`) and `nethackers/mutator:latest` ×5 (`cli.py:281,308`, `launch.py:71`, `evolve_form.py:48`, `Makefile:9`) (**K5**). The arena tag is machine-global and embeds `src/`, so worktree A's eval silently runs worktree B's scorer after B rebuilds — the already-experienced stale-image failure (**surface 5**).
- **Repo name** — `"nethacker"` in `cli.py:405` and `launch.py:83`; the evolve path never passes it (`launch.py:185` calls `_publisher_for(params.owner, rid)` with no `repo_name`), so evolve has **no override at all** (**K6**).
- **GitHub App client id** — `Iv23liWooDi2WlkrDAOw` in `hubclient/register.py:33-34` (read from env **at import time**) must name the same App as the hub's `NETHACKERS_CLIENT_ID` (`hub/api.py:358`) (**K4**).

Parallel worktrees (this repo runs as orca worktrees: tripletail, bass, sponge, wolfeel) collide on all five surfaces: one port 8000 (**1**), dirname-derived compose projects and their `hubdata` volumes (**2**), a shared `~/.nethackers/evolve` runs/store (**3** — the overnight parallel runs already interleaved in the TUI and cross-polluted eval timing), one `<owner>/nethacker` publish repo (**4**), and the machine-global `arena:dev` tag (**5**).

**Auth.** Even with a local stack up, the stub-auth local hub **cannot validate a real registration**, so the register→board half of the loop has never been testable locally. Two independent walls, both by construction: the register ladder resolves the caller's login and requires `owns_repo(login, reference.repo)` (`hub/validate.py:149-152`, `hub/auth.py:75-83`) — stub logins are `"dev"`, which owns nothing; and `commit_exists` hits the **real** `https://api.github.com/repos/{owner}/{name}/commits/{sha}` with the caller's Bearer token (`hub/github.py:24-37`) — `create_default_app` injects no fake (`hub/api.py:359` returns `create_app(store, auth)`, leaving the default `git_factory = lambda token: GitHubRead(token)`, `api.py:133`), and a stub token 401s against GitHub, surfacing as a 502.

This spec introduces the missing model. One `Stage` value subsumes the three problems the team has been solving ad-hoc — per-worktree local-stack isolation, the local↔prod testing seam, and writer/reader config drift — and, with a real-auth local hub mode on top of it, makes the full local loop runnable end to end.

## Goal

- **One frozen `Stage` dataclass** whose field defaults ARE prod, loaded through one precedence chain (defaults < stage file < env < flag), consumed by CLI, TUI, harness, and — via compose as the adapter — the hub container.
- **Per-worktree isolation for free**: `make up` in any checkout allocates a stable port + compose project + data root + throwaway repo/image names into a gitignored `.env.stack`; everything in that worktree auto-targets its own stack.
- **A real-auth local hub mode** (`make up HUB_AUTH=github`): real `GitHubAppAuth`, real commit validation, local sqlite — the sole difference from prod is the DB. Register→board fidelity becomes a config flip, not new code.
- **A runnable end-to-end validation**: the full evolve loop against the worktree stack with a real operator, publishing to a fresh `nh-dev-<slug>` repo, checked by plumbing invariants.
- Three active bugs fixed as a consequence: the two contradictory default hubs (K7), the broken port override (K2), and the machine-global arena tag (surface 5).

## Core model: the Stage dataclass

A new `src/nethackers/config.py` — pure stdlib, no new dependency:

```python
@dataclass(frozen=True)
class Stage:
    name: str = "prod"                                # display id: "prod" or the worktree slug
    hub_url: str = "https://nethackers.dunnolab.ai"   # S1/S2 → kills K7
    hub_port: int = 8000                              # S3/S18 → kills K2 (compose/make side)
    compose_project: str = "nethackers"               # S17 — see note below
    data_root: Path = Path.home() / ".nethackers" / "evolve"   # S5/S6 → kills K1
    repo_name: str = "nethacker"                      # S7 → kills K6
    arena_image: str = "nethackers/arena:dev"         # S14 → kills K5
    mutator_image: str = "nethackers/mutator:latest"  # S15 → kills K5
    github_client_id: str = "Iv23liWooDi2WlkrDAOw"    # S13 → kills K4 (public App id — config, not a secret)

    @property
    def runs_dir(self) -> Path: return self.data_root / "runs"
    @property
    def store_dir(self) -> Path: return self.data_root / "store"
```

- **Field defaults ARE the prod stage.** No prod profile file exists anywhere; `Stage()` with no inputs is exactly today's behavior, byte for byte (the golden test pins this).
- **Derived paths are properties**, so the harness writer (`launch.py:131,136`) and the TUI reader share one expression — the structural fix for K1.
- **No secret is ever a `Stage` field.** Every field is printable. Tokens, keys, and logins stay in their existing homes (see Secrets).
- `hub_port` and `compose_project` have no Python consumer — compose and make read them from the stage file — but they live on `Stage` so the generator, the loader, and the docs share one schema. `compose_project`'s default is nominal: the prod path never exports it, and a bare `docker compose up` keeps compose's dirname-derived project.
- Two module constants, `DEV_TOKEN = "dev-token"` and `DEV_OWNER = "dev"`, centralize the client-side stub-identity fallbacks (K3's three copies) used when no one is logged in. They are the offline stub hub's identity and nothing more — deliberately **not** `Stage` fields (a protocol pair with the stub hub's identity map, not per-stage values); the fourth copy (`compose.yaml:18`) is killed by a drift test.
- **Publishing is gated by an explicit `--offline` flag, not by the owner name.** Today `_publisher_for` silently disables publishing whenever the owner is `"dev"` (`launch.py:94`) — an overloaded sentinel that conflates *who you are* with *should I publish*. That magic is replaced by `--offline` (harness `EvolveParams.offline`): it suppresses publish **and** register independent of identity, so a **logged-in** user can iterate on strategy locally without pushing experimental programs to GitHub. Publishing otherwise requires a real login; when no one is logged in (`DEV_OWNER`), publishing is impossible, so the run is offline by necessity — and the CLI says so plainly, rather than silently recording `local-only`. `--offline` gates only the publish/register writes; the loop still reads whatever hub is configured for cell-seeding.
- `hubclient/register.py`'s import-time `DEFAULT_CLIENT_ID = os.environ.get(...)` (`register.py:34`, defaulted into `device_login` at `register.py:62`) becomes a call-time `load_stage().github_client_id` resolution — the S13 import-order gotcha goes away with the literal.

## Storage & selection

### `.env.stack` — the stage is a file of env vars, not a name in code

A per-worktree, gitignored, flat `KEY=VALUE` file at the worktree root:

```
# generated by scripts/stack.py — this worktree's stage; gitignored, reused verbatim by `make up`
NETHACKERS_STAGE=tripletail
NETHACKERS_HUB=http://localhost:28417
NETHACKERS_HUB_PORT=28417
COMPOSE_PROJECT_NAME=nethackers-tripletail
NETHACKERS_DATA_ROOT=/Users/vokneruk/orca/workspaces/nethackers-v1/tripletail/.nethackers
NETHACKERS_REPO_NAME=nh-dev-tripletail
NETHACKERS_ARENA_IMAGE=nethackers/arena:tripletail
NETHACKERS_MUTATOR_IMAGE=nethackers/mutator:latest
```

Flat `KEY=VALUE` is deliberate: docker-compose interpolation, the Makefile (`set -a; . ./.env.stack`), and Python all read it natively — that is what makes one file the single source for CLI + TUI + harness + compose. A TOML or YAML profile would need a compose-side adapter, recreating the writer/reader split this work exists to kill.

`NETHACKERS_HUB` is **derived from `NETHACKERS_HUB_PORT` at generation time** — one writer, so URL and port can never drift (buro's key trick, the same discipline that fixes K2). `NETHACKERS_MUTATOR_IMAGE` is written at the shared default: the mutator image also embeds repo code (the arena+contracts parity kit), but it changes rarely and cross-worktree staleness there has not bitten; the line documents the knob for when it does.

Two `.gitignore` additions ship with the generator: `.env.stack` and `.nethackers/`. Neither is covered today — the existing `.env` / `*.env` patterns do not match `.env.stack` (it ends in `.stack`), and `.env` itself stays reserved for secrets, so the two never share a file.

### `scripts/stack.py` — the allocator (~80 lines, stdlib)

A single-port trim of buro's `worktree_stack.py`. Kept verbatim from buro: the sha256 seed (`hashlib.sha256(str(worktree.resolve()).encode())` — **not** builtin `hash()`, which is PYTHONHASHSEED-salted), the bind-probe `port_free` (`socket.bind(("127.0.0.1", port))`), the `KEY=VALUE` render/parse pair, the slug sanitizer (lowercase, non-alnum → `-`, strip), and the **reuse-the-file-verbatim rule** (if `.env.stack` exists, parse and return it — stable ports across restarts; the file is the allocation record). Dropped: the 16-port block model, the slot contract, the multi-URL derivation, and the teardown label sweep — buro has ten host ports, nethackers has one (the hub).

Allocation: `port = 28000 + int.from_bytes(sha256_digest[:4], "big") % 1000`, then scan forward (wrapping within 28000–28999) for the first bindable port. Identity: `slug = sanitize(basename)`; `COMPOSE_PROJECT_NAME = nethackers-<slug>`; `NETHACKERS_STAGE = <slug>`; data root `<worktree>/.nethackers` (the in-checkout gitignored-state pattern of `.venv`/`target/`: isolation for free, dies with the worktree). The throwaway publish repo is **`nh-dev-<slug>`** — the same worktree identity that drives the compose project and the port seed, and a name whose absence on GitHub is guaranteed enough to exercise the repo-creation path (see Validation). Running the script is idempotent; teardown needs no special handling because the compose project name is derived purely from the path — `make hub-down` regenerates the same name even if `.env.stack` was deleted.

### The loader — pip's documented precedence

```python
def load_stage(cwd: Path | None = None, environ=os.environ) -> Stage:
    values = _defaults()                                 # 1. prod defaults (the dataclass)
    values |= _parse_env_file(_find_stack_file(cwd, environ))  # 2. .env.stack, walking up from cwd
    values |= _from_env(environ)                         # 3. NETHACKERS_* process env
    return Stage(**values)                               # 4. CLI flags win last (argparse defaults come from this)
```

Built-in defaults < `.env.stack` < process env < CLI flag — the chain pip documents, and the one this codebase already implements correctly for output format (`hubclient/output.py:69`: flag > `$NETHACKERS_OUTPUT` > TTY). Env keys map 1:1 to fields (`NETHACKERS_STAGE`→`name`, `NETHACKERS_HUB`→`hub_url`, `NETHACKERS_HUB_PORT`→`hub_port`, `COMPOSE_PROJECT_NAME`→`compose_project`, `NETHACKERS_DATA_ROOT`→`data_root`, `NETHACKERS_REPO_NAME`→`repo_name`, `NETHACKERS_ARENA_IMAGE`→`arena_image`, `NETHACKERS_MUTATOR_IMAGE`→`mutator_image`, `NETHACKERS_CLIENT_ID`→`github_client_id`). The only casts are one `int()` and one `Path()`; unknown keys in the file are ignored. `_default_hub()` (`cli.py:108-109`) is deleted — the loader's env layer subsumes its `$NETHACKERS_HUB` read.

### Activation is implicit — with a visible indicator

`_find_stack_file` walks parent directories from cwd and activates the first `.env.stack` it finds — git/Cargo/npm/compose behavior: your cwd picks your stack. This is action-at-a-distance by design, so the design **requires** the mitigation:

- **CLI**: when `stage.name != "prod"`, `_run` (`cli.py:429`, right after `parse_args`) prints one dim line to stderr — `stage: tripletail · hub http://localhost:28417` — showing the stage name and the *effective* hub (`args.hub`, i.e. after any flag override). Stderr keeps `-o json` pipes clean.
- **TUI**: the idbar prefix (`tui/app.py:95` and `:205`) gains the stage name — `@vkurenkov · hub:localhost:28417 · stage:tripletail` — for non-prod stages.
- **`--prod` (the easy prod escape)**: a top-level flag forcing the built-in defaults — the prod stage — and ignoring any discovered `.env.stack`. This is the frequent case of hitting the global hub from inside a worktree (`nethackers board --prod`, `nethackers pull … --prod`): it bypasses discovery entirely, so hub, data root, repo, and images are *all* prod, not just the URL. Resolved by a lightweight `sys.argv` pre-scan before the parser is built (the stage must be known to compute argparse defaults), then `load_stage(ignore_file=True)`.
- **Env hatches**: `NETHACKERS_STAGE_FILE=/path` targets a specific file; `NETHACKERS_STAGE_FILE=` (empty) ignores any file — the env form of `--prod`. `--hub <url>` still overrides only the hub, for an ad-hoc read against another hub without otherwise leaving the stage.

### Identity display: authed vs guest

Being unauthenticated is a first-class state: you can browse boards, `pull` public repos, and run `evolve --offline`; only publish/register need a login. The UI says so honestly and never leaks the internal stub token.

- The anonymous fallback `DEV_OWNER="dev"` is an internal protocol value for the stub hub, **never a user-facing identity**. The TUI already renders the unauthenticated state as **`guest`**, not `@dev` (`tui/app.py:93,203`); this design keeps that and adds the stage — `guest · hub:localhost:28417 · stage:tripletail`.
- **`whoami` becomes the "where am I pointed" command.** Today it prints `@<login>` or `not logged in — run nethackers login` (`cli.py:467-475`). It gains the active **stage + hub** on both branches, answering what implicit activation raises — *which hub, as whom*:
  - logged in → `@vkurenkov · stage:tripletail · hub:localhost:28417`
  - guest → `not logged in (guest) · stage:tripletail · hub:localhost:28417 — browse/offline only; run \`login\` to publish`
  - `--json` gains `stage`, `hub`, and `authenticated` beside the existing `login`.
- The CLI stage-indicator line (Activation) and `whoami` share one formatter, so "where am I" reads the same everywhere.

## The three stages

| Stage | Expression | Code required |
|---|---|---|
| **prod** | No file anywhere: `Stage()` defaults. `nethackers board` outside a worktree behaves exactly as today. | none — it IS the defaults |
| **per-worktree local** | The generated `.env.stack`. One worktree identity covers all isolation surfaces: own hub port + compose project (+ project-scoped `hubdata` volume), own `data_root` (the TUI in that worktree shows only its runs), own `nh-dev-<slug>` repo and `arena:<slug>` image. | `scripts/stack.py` output |
| **future staging** | A hand-written env file on the staging host (or exported vars in CI): `NETHACKERS_STAGE=staging`, `NETHACKERS_HUB=https://staging...`, `NETHACKERS_CLIENT_ID=<staging App>`. | zero — no profile registry to extend |

## How each entrypoint reads it

| Entrypoint | Change | Kills |
|---|---|---|
| **CLI** | `stage = load_stage()` once in `_run` (`cli.py:429`), preceded by a `sys.argv` pre-scan for `--prod` (resolve with discovery off → prod defaults); `_build_parser(stage)` takes its defaults from it: `--hub` (`cli.py:230`, replacing `_default_hub()`), `--image` (`:269,321,414`), `--mutator-image` (`:281,308`), `--workdir` (`:331`), `--repo-name` (`:405`); token/owner fallbacks (`:544-545`) use `config.DEV_TOKEN`/`DEV_OWNER`. Flags keep working and win last; the existing `argparse.SUPPRESS` subcommand-flag trick (`cli.py:193-207`) is untouched. Stale help at `cli.py:197-198` corrected. | K7, K5, K6, K3 (client), K2 (client side) |
| **TUI** | Both `_RUNS_DIR` module constants deleted (`home.py:34`, `runs.py:21`); the read sites (`home.py:197`, `runs.py:182`) use `load_stage().runs_dir`. `evolve_form._MUTATOR_IMAGE` (`:48`) and its dev fallbacks (`:326-327`) repoint to config. Idbar shows the stage. | K1, K5, K3 (client) |
| **harness** | `EvolveParams` stage-derived defaults (`launch.py:62-66,71`) become `field(default_factory=lambda: load_stage().<field>)` — late-bound, never import-time. New `repo_name` field (default from stage) threaded into `_publisher_for` at `launch.py:185`, giving evolve the override it never had. New `offline` field gates the publisher (replacing the `owner=="dev"` magic at `launch.py:94`). `_default_workdir` (`launch.py:51-52`) returns `str(load_stage().data_root)`. | K7, K6, K1 |
| **hub server** | **Unchanged.** `create_default_app` stays env-only 12-factor (`NETHACKERS_DB`/`NETHACKERS_LOAD_FIXTURES`/`NETHACKERS_STUB_IDENTITIES`/`NETHACKERS_CLIENT_ID`, `hub/api.py:345-358`) — correct for a containerized process. Compose is the adapter that turns stage values into container env. The server never reads the stage file. | — (by design) |
| **compose / Make** | New `stack` target runs `scripts/stack.py`; `hub`/`hub-down`/`hub-reset`/`wait-hub`/`up` recipes start with `set -a; . ./.env.stack 2>/dev/null || true;` so compose sees `COMPOSE_PROJECT_NAME` + `NETHACKERS_HUB_PORT` and `wait-hub` curls `localhost:$${NETHACKERS_HUB_PORT:-8000}` (`Makefile:65`; echo at `:56` prints `$$NETHACKERS_HUB`). `compose.yaml`'s port line (`:13`) already interpolates. A top-of-file `-include .env.stack` feeds make-level tag vars: `ARENA_IMAGE ?= $(or $(NETHACKERS_ARENA_IMAGE),nethackers/arena:dev)` (`Makefile:7`), so `make arena` builds the worktree's own tag while `make arena ARENA_IMAGE=x` still wins. Bare `docker compose up` with no stack file keeps today's behavior — the fallback property. | K2, surfaces 1, 2, 5 |

## Running the whole system locally

The point of the foundation. The full path, from a fresh worktree to a program on a board — every value below supplied by the stage, no flags:

```
cd <worktree>
make up HUB_AUTH=github     # allocate .env.stack · build arena:<slug> · hub on the stage port, real auth, empty DB
make mutator                # once per machine — the operator sandbox image
nethackers login            # once per machine — GitHub device flow (user-global credentials)
nethackers evolve val-dwa-law-fem --seed roots/autoascend --operator claude --iterations 2
nethackers leaderboard --objective val-dwa-law-fem     # the run's programs, on THIS worktree's board
nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check # fetch an exact published commit back
```

The mutator is deliberately **not stubbed**: the bugs this path exists to catch live in the real agent × sandbox × eval interaction — the codex login-shell PATH loss, operator auth injection (`harness/auth_inject.py`), stale-image drift, the 401-token-refresh registration bug — none of which a fake operator reproduces. The loop's register-all behavior (`harness/loop.py:303-315`: publish + register **every** scored program, improved or not) makes the plumbing deterministic even though the operator is not: one completed iteration always yields at least one publish + registration. Publishing goes to the throwaway `nh-dev-<slug>` repo on the per-run ref `evo-harness-v1/<run-id>` (`launch.py:99`); the TUI opened in the worktree shows exactly this run under its own `data_root`.

## Real-auth local hub mode

Because the stub hub structurally cannot validate a real registration (Problem, Auth), register→board fidelity requires the local hub to run **real `GitHubAppAuth`** — everything real except the database. This is a supported configuration the Stage model expresses through env, with compose as the adapter; no server code changes.

The compose file splits three ways (compose's canonical base/override pattern):

- **`compose.yaml`** (base) — service, `ports: ["${NETHACKERS_HUB_PORT:-8000}:8000"]`, `hubdata` volume, `NETHACKERS_DB: /data/hub.db`. No auth env.
- **`compose.override.yaml`** (checked in — compose auto-merges it when no `-f` is given) — the offline dev default, exactly today's env: `NETHACKERS_STUB_IDENTITIES: '{"dev-token":"dev"}'` + `NETHACKERS_LOAD_FIXTURES: "1"`. Bare `docker compose up` therefore behaves exactly as today, and `tests/test_compose_smoke.py` (which relies on stub + fixtures, `:31-32`) is untouched.
- **`compose.github.yaml`** — real auth: `NETHACKERS_CLIENT_ID: ${NETHACKERS_CLIENT_ID:-Iv23liWooDi2WlkrDAOw}`. No stub map (unset → `create_default_app` selects `GitHubAppAuth`, `api.py:353-358`), no fixtures (prod loads none; parity means an empty DB). The interpolation default is the same App id as `Stage.github_client_id` — one App on both sides of the device flow (K4) — pinned equal by a drift test.

Makefile: `HUB_AUTH ?= stub`; the `hub` recipe runs bare `docker compose up -d --build` for stub, and `docker compose -f compose.yaml -f compose.github.yaml up -d --build` for `HUB_AUTH=github` (an explicit `-f` list disables the auto-override). `make up HUB_AUTH=github` is the whole flip. A no-fixtures hub still serves the objective catalog and passes `wait-hub` — `GET /objectives` and the register ladder's batch check both read the **code-level** `CATALOG` (`api.py:197`, `validate.py:118`), and registration upserts its own objective row (`validate.py:182`).

Division of labor, stated plainly: **stub mode** remains for offline board/UI/fixture/migration work — fast, no network, fake identities. **Real-auth mode** is for anything touching register→board: the hub resolves the caller's real login, `owns_repo` passes because the user actually owns `nh-dev-<slug>`, and `commit_exists` finds the actually-pushed public commit. The sole difference from prod is `NETHACKERS_DB` pointing at a local sqlite volume.

## Validation: full-loop invariants

A real operator is non-deterministic, so the validation checks **plumbing invariants, not scores**. Two layers:

**The repo-creation probe.** `ensure_repo` (`hubclient/publish.py:57-75`) probes with `gh repo view` and creates the repo public when absent — a path never exercised by the user's real account, whose `nethacker` repo already exists. The throwaway `nh-dev-<slug>` name is a fresh identity by construction, so the first local full-loop run **necessarily** drives the create path. Cleanup is `gh repo delete <login>/nh-dev-<slug> --yes` when done.

**The full-loop invariant checklist** — after one `nethackers evolve ... --iterations 1` against the real-auth worktree stack:

1. **Repo auto-created, public**: `gh repo view <login>/nh-dev-<slug> --json visibility` → `PUBLIC` (a private repo would 404 the hub's commit check and silently drop every registration — the exact failure `ensure_repo`'s docstring guards).
2. **Commit on the per-run ref**: `git ls-remote https://github.com/<login>/nh-dev-<slug> 'refs/heads/evo-harness-v1/*'` lists the run's sha — the parallel-safe one-ref-per-run contract.
3. **Registration accepted**: the run log shows `registered`, not `local-only`/`hub publish/register failed` (`loop.py:313,325`); `nethackers search --owner <login>` lists the solution — meaning the full ladder passed with real auth: login resolved, `owns_repo` ✓, `commit_exists` ✓ against api.github.com.
4. **Atoms per identity, board updates**: `nethackers leaderboard --objective val-dwa-law-fem` shows the program with its self-reported dev score.
5. **Pull fetches the exact commit**: `nethackers pull <login>/nh-dev-<slug>@<sha> /tmp/check` succeeds and the clone's HEAD equals `<sha>` — confirming fetch-by-SHA on a non-default ref (the archive design's open risk 1).

**Implementation status**: this ships as the rollout's real-auth step (step 5) — a *documented, runnable manual procedure* (`docs/local-stack.md`), which this foundation makes a five-command session. Automating it later is a pytest marker joining the existing live family — a `github_live` marker excluded from `make test` exactly like `claude_live`/`codex_live` (`Makefile:73`) — an implementation detail of the same design, not a separate spec.

## Self-hosting (documented byproduct)

The real-auth local hub **is** a private instance: `compose.yaml` + `compose.github.yaml` on any persistent host, `NETHACKERS_CLIENT_ID` pointing at your own GitHub App, `NETHACKERS_HUB=https://your-host` in the env of every client — that is the entire recipe, and the staging row of the three-stages table is the same file. The per-worktree stage is just the ephemeral, laptop-scale case of self-hosting. `docs/local-stack.md` states this in one section; no code is built for it.

## Secrets

**Nothing moves.** GitHub user tokens stay in `~/.nethackers/credentials.json` (0600, user-global — one login shared by all worktrees is correct); operator logins stay mounted/injected per run (`harness/auth_inject.py`); the evaluator seed key stays on the evaluator host (popped before untrusted code at `arena/sandbox.py:59`); prod CD secrets stay in the GitHub Actions `production` environment. The model's guarantee is structural: `Stage` has no secret-typed field, every field is printable, the stage file is machine-generated (a human never pastes a token into it), and `.env` — the conventional gitignored secrets spot — is a different file from `.env.stack`. `NETHACKERS_CLIENT_ID` looks secret-shaped but is a public OAuth client id; the prod VM's env file already holds only non-secrets, and this design preserves that property.

## Rollout

**One PR.** Not a data migration — no atoms/schema change, no DB rewrite (that is the *separate*, already-landed MAP-Elites `objective_digest` migration, unrelated to this work). It is config plumbing, built in the order below as a sequence of self-contained commits so the diff reviews step by step and any commit reverts on its own, but shipped as a single change on one branch off `main`. Rollback is deleting a file or reverting a commit; no part touches the hub schema, the deploy path, or prod compose.

1. **`config.py` + `--offline`.** `Stage` + `load_stage()` (defaults + env layer only; no file reading yet) + `DEV_TOKEN`/`DEV_OWNER`. Repoints every literal named in the entrypoint table: `cli.py:230` (deleting `_default_hub`, `:108-109`), `:269,281,308,321,331,405,414,544-545`; `launch.py:51-52,62-66,71,83,185`; `home.py:34`; `runs.py:21`; `evolve_form.py:48,326-327`; `register.py:33-34,62`. Fixes the stale help (`cli.py:197-198`). Golden test pins `Stage()` == today's literals (the config repoint is zero-behavior-change). Also the explicit **`--offline`** flag (`EvolveParams.offline`) replacing the `owner=="dev"` publish sentinel at `launch.py:94`, with its own publish-gate test. Kills K1, K3 (client), K5, K6, K7 **as drift**.
2. **The worktree stack.** `scripts/stack.py`, the `stack` make target, recipe-level `set -a; . ./.env.stack` sourcing, the `wait-hub`/echo port fix (`Makefile:56,65,68`), `.gitignore` += `.env.stack`, `.nethackers/`. Generated keys: `NETHACKERS_STAGE`, `NETHACKERS_HUB`, `NETHACKERS_HUB_PORT`, `COMPOSE_PROJECT_NAME`, `NETHACKERS_DATA_ROOT`. `make up` per worktree yields an isolated port + project + volume. Kills K2 and surfaces 1–2.
3. **The loader reads the file.** `_find_stack_file` walk-up + the `NETHACKERS_STAGE_FILE`/`--prod` bypasses + the two indicators (CLI stderr line in `_run`; idbar at `app.py:95,205`) + the `whoami` stage/hub enrichment (`cli.py:467-475`). Every command inside a worktree auto-targets its stack and its `data_root`; `--prod` is the one-flag escape back to the global hub. Kills surface 3 and the "evolve pointed at prod with dev-token" bug class.
4. **Throwaway names.** Generator additionally writes `NETHACKERS_REPO_NAME=nh-dev-<slug>`, `NETHACKERS_ARENA_IMAGE=nethackers/arena:<slug>`, `NETHACKERS_MUTATOR_IMAGE=nethackers/mutator:latest`; Makefile gains `-include .env.stack` + the `$(or ...)` tag defaults. Kills surfaces 4–5 (the arena suffix is cheap — only the `src/` COPY layers above the shared `nle-base` are per-worktree).
5. **Real-auth hub + the recipe.** Split `compose.yaml` into base + `compose.override.yaml` (stub, today's env verbatim) + `compose.github.yaml`; `HUB_AUTH` make switch; the two drift tests (stub map == `DEV_TOKEN`/`DEV_OWNER`; github overlay's default client id == `Stage().github_client_id`); `docs/local-stack.md` carrying the end-to-end recipe, the validation checklist, and the self-hosting section. Makes the Validation section a runnable session.

## Testing

- **Golden stage test**: `Stage()` equals today's literals field by field (`hub_url == "https://nethackers.dunnolab.ai"`, `arena_image == "nethackers/arena:dev"`, …) — the config-repoint no-behavior-change proof, and the permanent pin that prod is the defaults.
- **Allocator tests** (`scripts/stack.py`, injectable `is_free` — buro's pattern): same path → same seed port; occupied seed port → next free port (wrapping); existing `.env.stack` reused verbatim, no rescan; slug sanitization (`weird_Name.x` → `weird-name-x`).
- **Loader precedence test**: defaults < file < env, per field, including the `int`/`Path` casts and unknown-key tolerance.
- **Walk-up discovery test**: found from a nested cwd; `NETHACKERS_STAGE_FILE=/x` overrides the walk; `NETHACKERS_STAGE_FILE=` (empty) and `--prod` both ignore an existing file → `Stage()` defaults.
- **Identity display test**: `whoami` includes the active stage + hub on both the logged-in and guest branches, and the guest branch renders `guest`/`not logged in`, never `@dev`.
- **Drift tests** (kill the cross-format copies no import can reach): parse `compose.override.yaml` and assert its stub map is exactly `{DEV_TOKEN: DEV_OWNER}`; parse `compose.github.yaml` and assert its interpolation default equals `Stage().github_client_id`.
- **Publish-gate test**: `EvolveParams.offline=True` (from `--offline`) yields no publisher and no registration; a logged-in owner without it yields an active publisher — the explicit replacement for the `owner=="dev"` sentinel.
- The full-loop invariants stay a manual procedure in this design (Validation, implementation status) — the golden/allocator/loader/drift tests are what CI runs.

## Out of scope / what NOT to build

- **No pydantic-settings, no dynaconf.** pydantic is not a core dep (it arrives only via the `hub` extra); adding a compiled dependency chain to every CLI install to validate nine printable scalars is unjustified. dynaconf's profile machinery is untyped and framework-scale.
- **No named-profile registry, no `--stage prod|local|staging` switch.** Prod is code defaults and local is a generated file; a name-indexed profile table is a third representation with no consumer — 12-factor's combinatorial-explosion warning aims exactly at it. Selection by file presence + env override is enough.
- **No TOML/YAML config format.** Flat `KEY=VALUE` is the only format compose, `sh`, and Python all read natively.
- **No `nethackers config get/set`, no hot reload, no remote config.** The file is eight generated lines; `cat` is the UI.
- **No multi-port blocks, no Traefik/ddev-style name proxy.** One service, one port. The proxy is the >5-stacks endgame, not today's.
- **No file-reading in the hub server or the deploy path.** `create_default_app` stays env-only; prod deploy (env file + CD by digest) already works and holds no secrets in config.
- **No secrets features.** No encrypted stage files, no keychain integration, no secret `Stage` fields. Config references secrets by location; storage for them is explicitly out of scope.
- **No XDG migration.** Credentials stay at `~/.nethackers/credentials.json`; only the evolve-data root becomes stage-scoped.
- **The held-out verifier** (self-reported → verified tier) — separate work; this design neither needs it nor touches it.
- **No per-worktree GitHub automation beyond the name default.** `nh-dev-<slug>` is one generated line; publishing is gated by the explicit `--offline` flag (Core model), not by owner-name magic.

## Open risks

1. **Implicit discovery is action-at-a-distance.** The same command answers differently by `$PWD`. Mitigated by the required indicator (stderr line + idbar) — git's own model: cwd picks the config, the prompt shows the branch. The frequent "hit the global hub from inside a worktree" case has a dedicated one-flag escape — `--prod` (Activation) — so it never requires editing env or leaving the directory. If implicit discovery still proves too magic, the designed fallback is repo-scoped discovery: `_find_stack_file` accepts only a `.env.stack` sitting next to a `.git` entry — a one-function tightening, no schema change.
2. **The `data_root` move hides old runs.** Inside a worktree the TUI shows `<worktree>/.nethackers/runs` — pre-existing runs under `~/.nethackers/evolve/runs` become invisible *there* (they remain visible from any non-worktree cwd). Back-compat consideration, no code: `NETHACKERS_DATA_ROOT=$HOME/.nethackers/evolve nethackers` (or `NETHACKERS_STAGE_FILE=`) views the old corpus from anywhere; the recipe doc states this. No migration of old runs is attempted.
3. **Parallel `make up` can race on allocation.** Two fresh worktrees scanning simultaneously can pick the same port only if their path-derived seeds collide (uniform over 1000) *and* both scan before either hub binds. Accepted: the window is first-`make up` only (the file persists the claim thereafter), and recovery is deleting the loser's `.env.stack` and re-running. No lockfile is built for it.
4. **Slug collisions.** Same-basename worktrees under different parents share a slug — distinct ports (path-hashed) but the same compose project, repo, and arena tag. This is no worse than compose's current dirname behavior, and orca's generated worktree names make it unlikely; the escalation, if ever needed, is buro's researched hybrid `<basename>-<short-path-hash>` in the generator only — no reader changes.
