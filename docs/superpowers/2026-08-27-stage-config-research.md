# Stage/environment config for nethackers — research report

Repo: `/Users/vokneruk/orca/workspaces/nethackers-v1/tripletail` (worktree of `~/orca/projects/nethackers-v1`; siblings: bass, sponge, wolfeel).
All file:line references verified against this checkout on 2026-08-27.

---

## 1. Constant inventory

### 1a. Stage/env config — values that legitimately differ between prod / local / per-worktree

| # | Item | file:line | Current value | Consumers | Overridable today? |
|---|------|-----------|---------------|-----------|--------------------|
| S1 | Hub base URL (client side) | `src/nethackers/cli.py:109` | `https://nethackers.dunnolab.ai` (env fallback) | CLI, TUI (via `args.hub`), harness (`EvolveParams.hub`) | `--hub` flag + `$NETHACKERS_HUB` |
| S2 | Hub base URL (second default) | `src/nethackers/harness/launch.py:63` | `http://localhost:8000` (dataclass default) | harness, TUI evolve form (when field omitted) | constructor arg only — **disagrees with S1** (coupling K7) |
| S3 | Local hub host port | `compose.yaml:13` | `${NETHACKERS_HUB_PORT:-8000}` | compose | env var (compose interpolation only) |
| S4 | Hub bind host/port (server) | `src/nethackers/hub/server.py:17-18` | `0.0.0.0` / `8000` | hub entrypoint | `--host`/`--port` flags |
| S5 | Evolve workdir root | `cli.py:331` + `launch.py:51-52` | `~/.nethackers/evolve` | CLI (`--workdir`), harness (runs at `launch.py:131`, store at `launch.py:136`) | flag (CLI only; TUI form always uses the default) |
| S6 | TUI runs dir | `tui/screens/home.py:34`, `tui/screens/runs.py:21` | `~/.nethackers/evolve/runs` (module constants, twice) | TUI | **hardcoded** (coupling K1) |
| S7 | Publish repo name | `cli.py:405` (`--repo-name`), `launch.py:83` (default param) | `"nethacker"` | CLI `submit` (flag), harness evolve publish (**no flag** — `launch.py:185` never passes `repo_name`) | submit: flag; evolve: hardcoded |
| S8 | Stub identities | `compose.yaml:18` | `{"dev-token":"dev"}` | hub (`api.py:353` → `LocalStubAuth`) | env `NETHACKERS_STUB_IDENTITIES` |
| S9 | Fallback token/owner | `cli.py:544-545`, `launch.py:64-65`, `tui/screens/evolve_form.py:326-327` | `"dev-token"` / `"dev"` (three copies) | CLI, harness, TUI | `--token`/`--owner` flags; **must match S8** (coupling K3) |
| S10 | Hub DB path | `hub/api.py:345` (default `/data/hub.db`), `compose.yaml:16`, `deploy/compose.yaml:7` (`/data/hub.sqlite3` — prod differs) | see left | hub, compose | env `NETHACKERS_DB` |
| S11 | Fixture seeding | `compose.yaml:17`, `api.py:348` | `NETHACKERS_LOAD_FIXTURES=1` local, unset prod | hub | env |
| S12 | Auth-mode selection | `api.py:353-358` | stub if `NETHACKERS_STUB_IDENTITIES` set, else `GitHubAppAuth(NETHACKERS_CLIENT_ID)` | hub | env (implicit — presence of one var flips the mode) |
| S13 | GitHub App client id | `hubclient/register.py:33-34` | `Iv23liWooDi2WlkrDAOw` (public value, **not** a secret) | CLI login (client side), hub (server side, `api.py:358`) | env `NETHACKERS_CLIENT_ID` — read **at import time** client-side (gotcha); same var must name the same App on both sides (coupling K4) |
| S14 | Arena image tag | `cli.py:269,321,414`; `launch.py:62`; `Makefile:7` (`ARENA_IMAGE`) | `nethackers/arena:dev` (5 copies) | CLI, harness, Makefile | `--image` flags + make var; tag is **machine-global** — parallel worktrees clobber each other's build |
| S15 | Mutator image tag | `cli.py:281,308`; `launch.py:71`; `tui/screens/evolve_form.py:48`; `Makefile:9` | `nethackers/mutator:latest` (5 copies) | CLI, harness, TUI, Makefile | `--mutator-image` flags + make var (TUI copy hardcoded) |
| S16 | nle-base image tag | `Makefile:5` | `nethackers/nle-base:dev` | Makefile (build-time only) | make var |
| S17 | Compose project name | *implicit* — no top-level `name:` in `compose.yaml` | directory basename (`tripletail` here, `nethackers-v1` in the main checkout) | compose (containers, network, `hubdata` volume all namespaced by it) | `-p` / `COMPOSE_PROJECT_NAME` — currently unmanaged |
| S18 | Makefile hub URL | `Makefile:56,65` (`up` echo, `wait-hub` curl) | `http://localhost:8000` hardcoded | Makefile | **not** — ignores `NETHACKERS_HUB_PORT` (coupling K2: `NETHACKERS_HUB_PORT=9000 make up` starts on 9000, waits on 8000 forever) |
| S19 | Prod hub image ref | VM `/etc/nethackers/hub.env` (`NETHACKERS_HUB_IMAGE`), `deploy/compose.yaml:3` | `ghcr.io/dunnolab/nethackers-hub@sha256:…` | deploy compose | CD-managed env file (works well; leave alone) |
| S20 | Deploy knobs | `deploy/deploy-hub.sh:8-15` | `HUB_ENV_FILE`, `COMPOSE_FILE`, `PUBLIC_HEALTH_URL=https://nethackers.dunnolab.ai/healthz`, GHCR repo, retries | deploy script (VM side) | env with defaults (fine as-is) |
| S21 | Output format | `hubclient/output.py:69` | `$NETHACKERS_OUTPUT` → `"auto"` | CLI | flag > env > TTY — **already the layered pattern done right**; the model to copy |
| S22 | Debug traceback | `cli.py:756` | `$NETHACKERS_DEBUG` | CLI | env |
| S23 | Dictionary audio path | `hub/api.py:81` | `$NETHACKERS_DICT_AUDIO` → packaged mp3 | hub | env |
| S24 | Arena warnings | `arena/run.py:120` | `$NETHACKERS_ARENA_WARNINGS` | arena (in-image) | env |

### 1b. Genuine constants — not stage config, leave in code

| Item | file:line | Why it's a constant |
|------|-----------|---------------------|
| GitHub OAuth endpoints | `hubclient/register.py:26-27`; `api.github.com` in `credentials.py:69`, `hub/auth.py:63`, `hub/github.py:27` | GitHub's API, not ours |
| `HARNESS_VERSION = "v1"` | `harness/version.py` | protocol version, deliberately code-versioned (namespaces publish refs) |
| `ContainerCaps` (pids 512, 8g, 4 cpu, 28800s) | `harness/container_operator.py:35-39` | resource policy, not environment identity |
| `_HUB_TIMEOUT = 4.0` | `tui/screens/hub.py:32`, `home.py:37` (duplicated) | UI latency budget (the duplication is mildly annoying but harmless) |
| `PUBLIC_SECRET = "public"`, `ACTION_TIMEOUT_SECONDS = 5.0` | `hub/objectives.py:81,84` | protocol pins — the "secret" is deliberately public (seed determinism, not hiding) |
| Validation seed range (`start=1000`) | `harness/seeds.py:26` | eval protocol |
| entrypoint `bot.py`, root `.` | `cli.py:396-397,707` | solution-contract defaults |
| `max_parallel_evals=8`, `iterations=1`, operator `claude` | `cli.py`, `launch.py` | per-run tuning knobs, already flags |
| `SMOKE_PROJECT`/`SMOKE_PORT=8811` | `tests/test_compose_smoke.py:30-32` | test isolation (itself a mini-model of the worktree pattern: `-p` + port env) |
| bootcheck port 18099 | `deploy/deploy-hub.sh:62` | deploy-internal throwaway |
| Credentials path `~/.nethackers/credentials.json` | `hubclient/credentials.py:32` | **user-global identity, correctly NOT stage** — one GitHub login shared by all worktrees |

### 1c. Secrets — must never enter the stage/config store

| Item | Where | Handling today |
|------|-------|----------------|
| GitHub user access/refresh tokens | `~/.nethackers/credentials.json` (0600) | client-side file, `hubclient/credentials.py` |
| Evaluator seed key `NETHACK_ARENA_SECRET` | evaluator host only; defensively popped before running untrusted bots at `arena/sandbox.py:59` | env on the evaluator box; never in repo |
| `ANTHROPIC_API_KEY` | read pass-through `harness/discovery.py:233` | user env |
| Operator logins (codex `~/.codex`, Claude keychain → `CLAUDE_CODE_OAUTH_TOKEN`) | `harness/auth_inject.py` | mounted/injected per run |
| Prod CD secrets (SSH key, Tailscale OAuth, GHCR token) | GitHub Actions `production` env; GHCR token piped on stdin (`deploy-hub.sh`) | out of scope, already separated |

Note: `NETHACKERS_CLIENT_ID` (S13) *looks* like a secret but is a public OAuth client id — it is config. The prod VM's `/etc/nethackers/hub.env` currently holds only non-secrets (image pin + client id), which is why the deploy design passes the actual GHCR token on stdin. The stage model must preserve this property.

### 1d. Writer/reader couplings (places that must agree, with nothing enforcing it)

- **K1 — runs dir**: harness writes `<workdir>/runs` (`cli.py:331` → `launch.py:131`); TUI reads a *hardcoded* `~/.nethackers/evolve/runs` (`home.py:34`, `runs.py:21`). `--workdir X` today makes runs invisible to the TUI. Two independent hardcodes of the same path.
- **K2 — hub port**: `compose.yaml` honors `NETHACKERS_HUB_PORT`; `Makefile` `wait-hub`/`up` hardcode `:8000`; the CLI's local usage needs the user to hand-type `--hub http://localhost:<port>`. Three readers of one number, one writer.
- **K3 — dev identity**: compose's stub map `{"dev-token":"dev"}` must match the `"dev-token"`/`"dev"` fallbacks in three source files (`cli.py:544`, `launch.py:64-65`, `evolve_form.py:326`), or local registration 401s.
- **K4 — client id**: hub-side `GitHubAppAuth(NETHACKERS_CLIENT_ID)` and CLI-side `device_login(DEFAULT_CLIENT_ID)` must name the same GitHub App or login tokens won't resolve. Same env var name, two processes, no shared source.
- **K5 — image tags**: `nethackers/arena:dev` ×5 and `nethackers/mutator:latest` ×5 (incl. one comment-flagged copy in the TUI: "matches launch.EvolveParams.mutator_image / the CLI default").
- **K6 — repo name**: `"nethacker"` in `cli.py:405` and `launch.py:83`; the evolve path exposes no override at all.
- **K7 — two default hubs**: `cli._default_hub()` says prod; `EvolveParams.hub` says localhost. Whichever constructor you reach through decides where your run registers.

### 1e. Per-worktree collision surfaces (what actually breaks with parallel worktrees today)

1. **Port 8000** — only one worktree's hub can be up at a time (S3, K2).
2. **Compose project name** — dirname-derived: distinct for orca's random worktree names, but unpredictable, and the main checkout claims `nethackers-v1`; the `hubdata` volume follows the project name.
3. **`~/.nethackers/evolve`** — all worktrees share runs/ and store/ (S5/S6). Parallel overnight runs already interleave in the TUI and share the content store (see the run-integrity findings: eval noise across parallel runs).
4. **`<owner>/nethacker`** — every logged-in worktree publishes to the same real GitHub repo (branch namespace `evo-harness-v1/<run-id>` prevents races but not pollution).
5. **`nethackers/arena:dev`** — the tag is machine-global and embeds `src/`; worktree A's eval silently runs worktree B's scorer after B rebuilds (the already-experienced "stale-image rebuild" failure).

---

## 2. Best-practices survey (external, cited)

### 2a. 12-factor and its limits

- 12-factor demands "strict separation of config from code", stores config in env vars, and **explicitly rejects grouped named environments**: "developers may add their own special environments like joes-staging, resulting in a combinatorial explosion of config" — its alternative is granular, orthogonal env vars per deploy (https://12factor.net/config).
- Heroku open-sourced the methodology in Nov 2024 to modernize it (https://www.heroku.com/blog/heroku-open-sources-twelve-factor-app-definition/); critiques note env vars are schema-less, untyped, and leak to child processes, recommending files parsed at startup (https://allenap.me/posts/12-factor-app-config-in-the-environment-is-bad-advice); *Beyond the Twelve-Factor App* splits the factor into "Configuration, Credentials, and Code" (https://www.oreilly.com/library/view/beyond-the-twelve-factor/9781492042631/).
- Key reading: the 12-factor rejection targets **server deploys at scale**, not human-operated CLIs. Every serious CLI vendor ships named profiles anyway (below).

### 2b. Layered precedence in mature tools

- **git**: system < global < local < **worktree** (`extensions.worktreeConfig`) < env < `-c`, "last value found taking precedence" (https://git-scm.com/docs/git-config).
- **AWS CLI**: named `[profile x]` in `~/.aws/config`, selected by `--profile`/`AWS_PROFILE`; precedence CLI > env > credentials file > config file — and **plain config and secrets live in two different files** (https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html).
- **kubectl**: a context is a *named bundle* (cluster + user + namespace); `--kubeconfig` > `KUBECONFIG` > `~/.kube/config` (https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/).
- **npm**: "Command Line Flags, Environment Variables, npmrc Files, Default Configs", with project `.npmrc` > user > global > builtin (https://docs.npmjs.com/cli/v11/using-npm/config). **Cargo** walks `.cargo/config.toml` from cwd upward, deeper wins, env above files (https://doc.rust-lang.org/cargo/reference/config.html).
- **gh CLI**: prefs in `config.yml`, tokens in a separate `hosts.yml`/keyring; `GH_TOKEN` env wins (https://cli.github.com/manual/gh_help_environment).
- **pip** documents exactly the chain proposed below: "Command line options override environment variables, which override the values in a configuration file" (https://pip.pypa.io/en/stable/topics/configuration/); **black**: defaults < `pyproject.toml` < CLI (https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html).
- Server-side stage modeling: Rails checked-in `config/environments/*.rb` + `RAILS_ENV` with secrets apart in encrypted credentials (https://guides.rubyonrails.org/configuring.html); Spring `application-{profile}.yaml` + `spring.profiles.active` (https://docs.spring.io/spring-boot/reference/features/profiles.html); Django settings modules + the django-environ 12-factor pushback (https://docs.djangoproject.com/en/5.2/topics/settings/, https://django-environ.readthedocs.io/en/latest/).
- Config-vs-secrets: OWASP — secrets "littered throughout configuration files" is the named anti-pattern; centralize and rotate, apps retrieve at runtime (https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html). Even env vars are questionable for secrets (inherited by children, dumped in crash logs) (https://blog.diogomonica.com/2017/03/27/why-you-shouldnt-use-env-variables-for-secret-data/). The convergent rule: **config references a secret by name/location, never contains it** — AWS, gh, Rails, kubeconfig all implement it.
- Docker Compose reads a project-root `.env` for interpolation; documented precedence: shell env > `--env-file` > project `.env` (https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

**Industry convergence for prod + local + maybe-staging**: named profile *data* (a file) + gitignored local env file + env-var and flag escape hatches, secrets in a separate store. The container consumes pure env vars; the human-run tool resolves a profile *into* that env — "the profile is developer ergonomics, the env var is the wire format."

### 2c. Python config libraries — honest comparison

- **pydantic-settings v2** (v2.15.0, 2026-08-07; https://pypi.org/project/pydantic-settings/): customizable multi-source precedence via `settings_customise_sources`; TOML source built-in; FastAPI's docs bless it as the standard (https://fastapi.tiangolo.com/advanced/settings/). Gotchas: complex env values must be JSON; module-level `Settings()` singletons explode at import time (https://github.com/pydantic/pydantic-settings/issues/234). **Decisive local fact**: `pyproject.toml` core deps are only `httpx, rich, rich-argparse, textual` — pydantic arrives *only* via the optional `hub` extra (fastapi). Adopting pydantic-settings for shared config would push `pydantic` + compiled `pydantic-core` into every plain CLI/TUI install, to validate ~10 scalar fields.
- **dynaconf** (3.3.5, 2026-08-05, alive; https://pypi.org/project/dynaconf/): has the exact `[default]/[development]/[production]` profile feature out of the box, but no declared schema — mypy/IDE-invisible settings, long-open typing issues (https://github.com/dynaconf/dynaconf/issues/448), a by-design lazy global settings object, and no CLI-flag layer. Practitioner consensus: pydantic-settings for small typed projects, dynaconf for genuinely multi-source config at scale (https://dasroot.net/posts/2026/01/python-configuration-management-pydantic-settings-dynaconf/). For three fixed stages it buys nothing that ~30 lines of stdlib doesn't.
- **environ-config** (Hynek; 26.1.0, 2026-07-22): attrs-based, deliberately env-only, **no config-file support at all** (https://github.com/hynek/environ-config) — structurally wrong for a profile file.
- **Plain stdlib** — frozen dataclass + `os.environ` overlay + (if ever needed) `tomllib`: what pip/black hand-roll and document. Costs: you write the `int()` casts and the "flag given vs defaulted" handling yourself (this codebase already solved the latter with `argparse.SUPPRESS` — `cli.py:171-208`). For 10–20 scalar settings this is one ~80-line module; past that, pydantic starts paying for itself.
- CLI frameworks: click encodes "prompt > CLI > env > default_map(file) > default" natively (https://click.palletsprojects.com/en/stable/options/); argparse (used here) has no env layer — the established pattern is compute defaults from the merged settings, exactly what `cli.py` already does for `NETHACKERS_HUB`/`NETHACKERS_OUTPUT`.

### 2d. Per-worktree stack isolation

- **Compose project name is the isolation primitive** — prefixes containers, networks, *and named volumes*; precedence `-p` > `COMPOSE_PROJECT_NAME` > `name:` > directory basename (https://docs.docker.com/compose/how-tos/project-name/). Same-basename worktrees collide into one stack (https://www.kubeblogs.com/how-to-avoid-issues-with-docker-compose-due-to-same-folder-names-project-isolation-best-practices/).
- **Ports**: random ephemeral publishing (`ports: ["8000"]` + `docker compose port`) is zero-config but churns URLs every restart (https://docs.docker.com/get-started/docker-concepts/running-containers/publishing-ports/). Deterministic hash-derived ports are what the 2025–26 agent-worktree tool wave independently reinvented: claude-worktree-hooks (branch-name md5 → port in `.env.local`, https://github.com/tfriedel/claude-worktree-hooks), worktree-compose (base+offset per worktree, https://www.worktree-compose.com/), Barnacle (hash + collision-scan fallback, https://www.barnacle.ai/blog/2026-02-07-the-missing-piece-of-the-claude-code-workflow-isol), a TDS toolkit hashing the worktree *path* (https://towardsdatascience.com/ai-agents-need-their-own-desk-and-git-worktrees-give-it-one/). No mainstream tool standardizes hash→port; the mature alternative is a name-routing proxy (ddev's shared Traefik on 80/443 routing `<project>.ddev.site`, https://docs.ddev.com/en/stable/users/extend/traefik-router/; localias, https://github.com/peterldowns/localias) — polished but heavy below ~5 stacks.
- **Identity derivation**: branch-name hashes break on rename/detached HEAD; **path hashes are stable and unique** but unreadable (VS Code keys per-workspace state by path hash, and path moves orphan it — https://github.com/microsoft/vscode/issues/313681); basename is readable but collides. Practical hybrid: `<basename>-<short-path-hash>`. Git itself stores per-worktree state under basename-plus-uniquifier and offers per-worktree config via `extensions.worktreeConfig` (https://git-scm.com/docs/git-worktree).
- **Data dirs**: in-checkout gitignored state is the dominant pattern — `node_modules`, Cargo `target/` (https://doc.rust-lang.org/cargo/guide/build-cache.html), tox `.tox`, uv `.venv` (https://docs.astral.sh/uv/concepts/projects/layout/): isolation for free, cleanup = delete the worktree.
- **direnv** is the standard per-directory env layer (`.envrc`, explicit `direnv allow` trust gate, https://direnv.net/) — worth knowing, not required when the Makefile sources the env file itself.

### 2e. The sibling (buro) pattern, assessed

`/Users/vokneruk/orca/workspaces/buro/*/scripts/worktree_stack.py` (283 lines, pure stdlib) + `docs/worktree-stacks.md`:
sha256(worktree abs path)[:4] % 750 seeds a 16-port block in 20000–32000 → scan forward for the first fully-free block → persist to gitignored `.env.stack` (`COMPOSE_PROJECT_NAME=buro-<basename>`, `STACK_PORT_*`, derived URLs like `BURO_API_URL`) → `make up` sources it (`set -a; . ./.env.stack`) before `docker compose up`; compose refs `${STACK_PORT_*:-default}` so bare compose still works; teardown re-derives the project name so it survives a deleted `.env.stack`.

**Merits (verified in their docs/code):** deterministic-but-collision-checked ports; *persistence* makes ports stable across restarts (the file is the allocation record); **derived URLs live in the same file as the ports** — the cross-layer consistency problem (their `BURO_FRONTEND_URL` ↔ web port) is solved by generating both from one source, which is precisely nethackers' K1/K2 disease; plain-compose fallback; robust teardown by re-derivable name. **What doesn't transfer:** buro has 10 host ports (PG, ClickHouse, Kafka, MinIO…); nethackers has **one** (the hub). The block machinery, slot contract, and multi-URL derivation shrink to a single port + two derived values. Their sha256-of-path choice (not `hash()`, which is PYTHONHASHSEED-salted) and "reuse the file verbatim if present" rule are the parts worth copying exactly.

---

## 3. Recommended Stage model

### 3a. What a Stage is

One frozen stdlib dataclass in a new `src/nethackers/config.py` — **no new dependency** (pydantic is not a core dep and ~10 printable scalar fields don't justify promoting it):

```python
@dataclass(frozen=True)
class Stage:
    name: str = "prod"                                  # display/id: "prod" or the worktree basename
    hub_url: str = "https://nethackers.dunnolab.ai"     # S1/S2/K7 — the ONE default hub
    hub_port: int = 8000                                # S3/S18/K2 — local stack host port
    compose_project: str = "nethackers"                 # S17 — docker compose -p
    data_root: Path = Path.home() / ".nethackers" / "evolve"   # S5/S6/K1 — runs/ + store/ under it
    repo_name: str = "nethacker"                        # S7/K6 — publish target under the user's account
    arena_image: str = "nethackers/arena:dev"           # S14/K5
    mutator_image: str = "nethackers/mutator:latest"    # S15/K5
    github_client_id: str = "Iv23liWooDi2WlkrDAOw"      # S13/K4 — public App id (config, not secret)
```

Field defaults ARE the prod stage — no `[prod]` profile file exists anywhere. Rules: every field is printable (a secret can never be a `Stage` field); derived paths come from methods (`stage.runs_dir = data_root/"runs"`, `stage.store_dir = data_root/"store"`) so writer and reader share one expression.

The hub *server* keeps its env-only interface (`NETHACKERS_DB`, `NETHACKERS_LOAD_FIXTURES`, `NETHACKERS_STUB_IDENTITIES`, `NETHACKERS_CLIENT_ID` — `api.py:336-358`). That is correct 12-factor for a containerized process; compose is the adapter that turns stage values into container env. Don't teach the server to read files.

### 3b. Storage and selection: the stage is a file of env vars, not a name in code

A gitignored **`.env.stack`** at the worktree root (buro's exact name; `.gitignore` already covers `*.env` — keep `.env` itself reserved for secrets so the two never share a file), generated by a trimmed allocator script:

```
# generated by scripts/stack.py — per-worktree stage; gitignored, regenerated by `make up`
NETHACKERS_STAGE=tripletail
NETHACKERS_HUB=http://localhost:28417
NETHACKERS_HUB_PORT=28417
COMPOSE_PROJECT_NAME=nethackers-tripletail
NETHACKERS_DATA_ROOT=/Users/vokneruk/orca/workspaces/nethackers-v1/tripletail/.nethackers
NETHACKERS_REPO_NAME=nethacker-tripletail
NETHACKERS_ARENA_IMAGE=nethackers/arena:tripletail
NETHACKERS_MUTATOR_IMAGE=nethackers/mutator:latest
```

`scripts/stack.py` (~80 lines, stdlib, lifted from buro): port = sha256(abs worktree path)-seeded scan over one range (e.g. 28000–29000) for a **single** free port; reuse the file verbatim when it exists; project name `nethackers-<sanitized-basename>`. `NETHACKERS_HUB` is *derived from* `NETHACKERS_HUB_PORT` at generation time — one writer, no drift (buro's key trick).

**Selection = one loader, pip's documented precedence** (built-in defaults < stage file < process env < CLI flag):

```python
def load_stage(cwd=None, environ=os.environ) -> Stage:
    values = asdict(Stage())                      # 1. prod defaults (in code)
    values |= _parse_env_file(_find_stack_file(cwd))   # 2. .env.stack, walking up from cwd
    values |= _from_env(environ)                  # 3. NETHACKERS_* process env wins over the file
    return Stage(**values)                        # 4. argparse flags win last (defaults come from this)
```

- `_find_stack_file` walks parents from cwd (Cargo/npm-style); `NETHACKERS_STAGE_FILE=/path` or `NETHACKERS_STAGE_FILE=` (empty = ignore any file) short-circuits it.
- `_parse_env_file` is buro's 12-line `KEY=VALUE` parser. No TOML needed: compose interpolation, the Makefile (`set -a; . ./.env.stack`), and Python all natively read the same flat format — **that's what makes it the single source for CLI + hub + TUI + compose**.
- The only casts are one `int()` and one `Path()`; unknown keys are ignored (the file also carries `COMPOSE_PROJECT_NAME`, which only compose/make consume).

### 3c. How the three stages are expressed

- **prod** — no file anywhere: `Stage()` defaults. `nethackers board` outside a worktree behaves exactly as today.
- **per-worktree local** — the generated `.env.stack`. One worktree identity (the basename + path-hash port) subsumes all three isolation surfaces: hub port + compose project (+project-scoped `hubdata` volume), `data_root` inside the worktree (`.nethackers/`, gitignored — the `.venv`/`target/` pattern: isolation for free, dies with the worktree, and the TUI in that worktree shows only its runs), and throwaway `repo_name`/`arena_image` suffixed with the stage name. The arena tag suffix directly kills collision surface 5 cheaply (layers above shared `nle-base` are just the `src/` COPY).
- **future staging** — a hand-written env file on the staging host (or exported vars in CI): `NETHACKERS_STAGE=staging`, `NETHACKERS_HUB=https://staging...`, `NETHACKERS_CLIENT_ID=<staging App>`. Zero code change, no profile registry to extend. This is 12-factor's granular-env-vars answer *and* the AWS-profile answer at once, because the profile is just a file of those vars.

### 3d. How each entrypoint reads it

| Entrypoint | Change |
|---|---|
| CLI | `stage = load_stage()` once in `_run()`; argparse defaults come from it (`--hub` default `stage.hub_url`, `--workdir` default `stage.data_root`, `--image`/`--mutator-image`/`--repo-name` likewise). Flags keep working and win, incl. the existing `SUPPRESS` subcommand trick. Print a one-line dim stderr note when a stage file is active (`stage: tripletail · hub http://localhost:28417`) so the active stage is never a surprise. |
| TUI | delete both `_RUNS_DIR` constants; use `load_stage().runs_dir` (or a Stage handed down from the app, which already receives `hub`). Idbar already shows `hub:<host>`; add the stage name. |
| harness | `EvolveParams` defaults sourced from `Stage` (kills K7); `_publisher_for` takes `repo_name` from params (kills K6). |
| hub server | unchanged — env-only (compose passes the stage's values as env). |
| compose/Make | `make up`: `python scripts/stack.py && set -a && . ./.env.stack && docker compose up -d --build`; `wait-hub` curls `localhost:${NETHACKERS_HUB_PORT:-8000}` (kills K2). `compose.yaml` unchanged — it already interpolates `${NETHACKERS_HUB_PORT:-8000}`, and `COMPOSE_PROJECT_NAME` from the sourced file namespaces project + volume. Bare `docker compose up` still works (buro's fallback property). |

### 3e. Secrets

Nothing moves. Credentials stay in `~/.nethackers/credentials.json` (user-global — one login for all worktrees is correct); operator logins stay mounted/injected per run; the evaluator seed key and prod CD secrets stay where they are. The model's guarantee is structural: `Stage` has no secret-typed field, the stage file is generated (a human never pastes a token into it), and `.env` (the conventional secrets spot, already gitignored) is a different file from `.env.stack`. `NETHACKERS_STUB_IDENTITIES` remains a compose-level dev fixture, not a Stage field — though `Stage` centralizing `"dev-token"/"dev"` as the *client-side* fallback constants closes K3's three-copy drift.

---

## 4. Migration path (incremental, each step shippable alone)

1. **PR 1 — `config.py`, zero behavior change.** Add `Stage` + `load_stage()` (defaults only, no file reading yet). Point the ~18 duplicated literals at it: `cli.py` argparse defaults, `launch.py` `EvolveParams` defaults + `_default_workdir` + `_publisher_for`, both TUI `_RUNS_DIR`s, `evolve_form._MUTATOR_IMAGE`, the dev-token/dev fallbacks. Golden test asserting `Stage()` equals today's literals. Kills K1, K3(client side), K5, K6, K7 as *drift risks* immediately.
2. **PR 2 — worktree stack.** `scripts/stack.py` (single-port trim of buro's allocator) + Makefile sourcing + `wait-hub` port fix. `.env.stack` exists but Python doesn't read it yet; `make up` per worktree already gives isolated hub port + compose project + volume. Kills K2 and collision surfaces 1–2.
3. **PR 3 — the loader reads the file.** `_find_stack_file` walk-up + env overlay in `load_stage()`; the stage-active stderr/idbar indicator. Now `nethackers evolve`/`board`/TUI inside a worktree auto-target its own stack and its own `data_root`. Kills collision surface 3 and the "evolve accidentally pointed at prod with dev-token" class of run-integrity bugs.
4. **PR 4 (optional, when actually bitten) — throwaway repo + image tags.** Generator writes `NETHACKERS_REPO_NAME=nethacker-<stage>` and `NETHACKERS_ARENA_IMAGE=nethackers/arena:<stage>`; `make arena ARENA_IMAGE=...` already accepts the tag. Kills surfaces 4–5. Staging, if it ever exists, is a hand-written env file — no PR needed.

Rollback at any step is deleting a file or reverting one module; no step rewrites the hub, deploy, or compose files.

## 5. What NOT to build

- **No pydantic-settings, no dynaconf.** pydantic isn't a core dep; adding a compiled dependency chain to every CLI install to validate ten printable scalars is unsourced complexity. dynaconf's profile machinery is untyped and framework-scale (§2c). Revisit only if config grows nested/validated (it shows no sign of it).
- **No named-profile registry or `--stage prod|local|staging` switch.** With prod = code defaults and local = a generated file, a name-indexed profile table in code is a third representation with no consumer — and 12-factor's combinatorial-explosion warning is aimed exactly at it. Selection by *file presence* + env override is enough.
- **No TOML/YAML config format.** Flat `KEY=VALUE` is the only format compose, `sh`, and Python all read natively; a TOML profile file would need a compose-side adapter — recreating the writer/reader split this work exists to kill.
- **No `nethackers config get/set` subcommands, no hot reload, no remote config.** The file is 8 generated lines; `cat` is the UI.
- **No multi-port blocks, no Traefik/ddev-style name proxy.** One service, one port (buro needed 10 ports; nethackers needs 1). The proxy is the >5-stacks endgame, not today's.
- **No file-reading in the hub server or deploy path.** `create_default_app` stays env-only; prod deploy (env file + CD by digest) already works and holds no secrets in config.
- **No secrets features.** No encrypted stage files, no keychain integration, no secret fields on `Stage`. Config references secrets by *location* (unchanged paths); building storage for them is explicitly out of scope.
- **No XDG migration / moving `~/.nethackers`.** Credentials stay user-global; only the *evolve data* root becomes stage-scoped.
- **No per-worktree GitHub automation beyond a name default.** Publish is already correctly disabled for the anonymous `dev` owner; a suffixed `repo_name` default is one generated line, not a feature.

## 6. The one tradeoff to decide

**Implicit cwd-based stage discovery vs explicit-only activation.** The recommended loader makes `nethackers board` answer differently inside a worktree (its stack) than outside (prod) — great DX, matches Cargo/npm/direnv/compose's project-file discovery, but it's action-at-a-distance: the same command, different hub, decided by `$PWD`. The alternative — activate only via `NETHACKERS_STAGE_FILE`/sourcing (direnv-style explicitness) — is more honest but re-introduces the daily chore the stage exists to remove, and in practice people forget to source and silently hit prod (the current failure mode, inverted). Recommendation: implicit discovery **plus a visible indicator** (stage name on the TUI idbar; one dim stderr line on the CLI), which is how git itself behaves (your cwd picks the repo config and nobody is confused, because the prompt shows the branch). If that still feels too magic, the fallback position is: discovery applies only to *worktree checkouts of this repo* (file must sit next to `.git`), never to arbitrary directories.
