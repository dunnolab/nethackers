# Remote Hub + GitHub Login (Milestone 1 of the "global verified hub")

- **Date:** 2026-08-22
- **Status:** Design — approved in brainstorm, pending spec review.
- **Author:** Vlad + Claude (brainstorm session)
- **Topic:** Turn v1's local, stubbed hub into a real global hub on the
  provisioned `dunnolab` servers, authenticated by a real GitHub App, accepting
  registrations of public `repo@commit` solution links.

---

## 1. Why this exists

Three asks drove this: **login via GitHub**, a **global hub on a remote
server**, and **verification on the server**. Investigation showed all three are
the milestone this codebase was consciously built to grow into (the "M2b"
markers scattered through `hub/validate.py`, `harness/seeds.py`, and
`hub/auth.py`), and that a full, older implementation of them already exists in
the private `dunnolab/nethackers-internal` repo against two already-provisioned
VMs.

The work is **too large for one spec**, so it is decomposed into two milestones
with a clean dependency:

- **Milestone 1 (this doc):** real remote hub + GitHub login + a validated
  registry of public solution links. Independently useful: a trusted cohort can
  log in and register solutions to a live global hub.
- **Milestone 2 (separate spec):** the **held-out verifier** — an always-on
  worker on a separate host that leases promising registered solutions,
  evaluates them on secret held-out seeds, and publishes the authoritative
  scores/leaderboards. This is where "verification on the server" actually
  happens.

This doc specifies **Milestone 1 only.**

### Decisions locked in the brainstorm

| Axis | Decision |
|---|---|
| Audience | Trusted cohort now, public-ready later (don't bake in trusted-only assumptions) |
| What "verification" is | A background job re-evaluates *promising* solutions on **held-out seeds** (M2). M1 does provenance/identity only. |
| Compute topology | Hub (light web) on one VM; verifier worker on a **separate** host (option 3) |
| Production codebase | `nethackers-v1` (this repo) is production; reuse `-internal`'s deploy configs + protocol as reference |
| Server state | Both VMs are **wipeable** — fresh deploy, no migration |
| Repo shape | **One repo**; operator surface behind extras, invisible to casual users |
| Provenance | **The link is the identity** — `repo@commit`, no separate content hash |
| Ownership | You may register only a **public repo your login owns** |
| Login | Real GitHub App; **silent refresh** (login once) |
| Deploy | Reuse `-internal`'s hardened compose (hub+caddy), **CI → GHCR → digest-pinned pull** |
| CI | Add both an image-build workflow **and** a fast-suite/mypy/ruff test gate |

---

## 2. Current state (what already exists)

**Login — code done, unwired.** `hubclient/register.py:device_login` implements
the GitHub device flow; `hub/auth.py:GitHubAppAuth` validates a user token
against `GET /user`; `hubclient/credentials.py` stores `{login, token}` at
`~/.nethackers/credentials.json` (chmod 600); `nethackers login` exists
(`cli.py:387`). Gaps: the client id is the placeholder `Iv1.nethackers-dev`
(`register.py:33`), no real App is registered, `register` re-runs the device
flow every call instead of reusing the stored token (`register.py:105`), and
there is no refresh handling (tokens die at 8h).

**Hub — real, but stubbed for trust.** `hub/api.py:create_default_app` already
selects `GitHubAppAuth` when `NETHACKERS_CLIENT_ID` is set and
`NETHACKERS_STUB_IDENTITIES` is not. `POST /register` runs the `hub/validate.py`
§6 ladder but on `LocalStubGit` — **no real git, no code execution** — and the
only tier is `"self-reported"`. The CLI already points anywhere via
`--hub`/`$NETHACKERS_HUB` (default `http://localhost:8000`).

**Servers — provisioned (in `-internal`).**

| Role | Host | Notes |
|---|---|---|
| Hub VM | `45.91.237.200` → `https://nethackers.dunnolab.ai` | Ubuntu 26.04, 2 GiB RAM, Caddy auto-TLS, data at `/srv/nethackers/data`. "Do not run evaluation here." |
| Evaluator (M2) | `72.56.24.170` (`nethacker-eval`) | 4 CPU / 8 GiB, SSH-only, holds the private seed key. **Not used in M1.** |

`-internal` provides reusable deploy assets: `deploy/Caddyfile`,
`deploy/compose.yaml`, `scripts/provision.sh`, `scripts/deploy-hub.sh`,
`ops/vm.md`. M1 adapts these; it does **not** reuse `-internal`'s application
code (`nethackers-v1` is production).

---

## 3. Design

### 3.1 One repo, three audiences, zero secrets

v1 is already a monorepo; M1 makes the audience split explicit.

- **Contributor surface (default install):** `nethackers login / register /
  evolve / board / …`. Light deps (`httpx`, `rich`, `textual`). This is all a
  casual `pip install nethackers` and `--help` shows.
- **Operator surface (opt-in):** hosting a hub, running the M2 worker. Exposed
  as **separate console scripts** `nethackers-hub` / `nethackers-worker`,
  installed only with the `nethackers[hub]` / `nethackers[worker]` extras, and
  **absent from the contributor `--help`**. Self-hosting is documented in a
  secondary "Run your own hub" section — supported but unadvised (people can; we
  neither push nor stop them).
- **Shared engine:** `arena` / `contracts` / `harness`, imported by both.

**Secrets: none live in the repo, by construction.**

- The **held-out seed key** (M2's `NETHACKERS_SEED_KEY`) is host-only env on the
  evaluator box. Because seeds are a *keyed* HMAC
  (`arena/seeds.py:trajectory_spec`), the formula is safe to open-source and the
  seeds stay uncomputable without the key. *Publish the exam format, not the
  exam.*
- The **hub holds zero GitHub secrets** in M1 (user-token model — no client
  secret, no App private key).
- Bearer tokens (M2 worker token) are `openssl rand` at deploy, host-only env.
- The GitHub App **`client_id` is public** and may be baked into the package.

Reading the open repo tells an attacker the verification *process* (good) but
not the *seeds* (no key), and they still cannot mint a token for another login
or escape the candidate sandbox. The one historically-sensitive item — the
*validation* seed formula an evolver could read to overfit its **own** local
gate — is separate from the keyed held-out tier and already walled out of the
mutator image (commit `dae8ddf`; see memory `mutator-image-no-nethackers-package`).

**Structural change M1 introduces:** a `deploy/` directory (Caddyfile, compose,
systemd if needed, deploy script — repo files, **not** shipped in the wheel) and
the extras-gated operator entrypoints.

### 3.2 Production GitHub login

**Human step (out of repo, one time):** register the **"NetHackers Hub" GitHub
App** under `dunnolab` with exactly the settings from
`docs/superpowers/research/2026-08-09-github-oauth-vs-apps.md`:

- Repository permission **Contents: read** (Metadata: read auto-added), nothing
  else.
- **Enable Device Flow ✓**, **Expire user authorization tokens ✓**, **Webhook →
  Active ✗**, no installation required.
- Record the public **`client_id`**.

**Code changes (small — the seams exist):**

1. Replace the placeholder default client id (`register.py:33`) with the real
   one; keep the `NETHACKERS_CLIENT_ID` env override.
2. **Server auth flip** is configuration only: set `NETHACKERS_CLIENT_ID`, leave
   `NETHACKERS_STUB_IDENTITIES` unset → `create_default_app` uses
   `GitHubAppAuth`. Local dev/tests keep the stub default (no network).
3. **Unify the credential.** `nethackers login` runs the device flow *once* and
   stores the credential; `register` (and any authed call) **reuses the stored
   token**. The device flow fires only in `login`, or on-demand when an authed
   command runs while logged out.
4. **Silent refresh (login once).** Extend `Credentials` to
   `{login, access_token, refresh_token, expires_at}`. Authed commands
   auto-refresh on expiry/401 using the refresh token — which needs **no client
   secret** for device-flow-born tokens — and re-persist the rotated pair
   atomically (a refresh invalidates the old pair; persist the new one before
   using it). Old-format `{login, token}` files read gracefully (trigger a
   re-login). `credentials.json` stays chmod 600.

**Security property (stated plainly):** attribution is server-enforced, not
client-claimed. The hub resolves `token → login` itself and stores
`owner = that login`, rejecting the registration unless `login` owns the repo.
The CLI's `--owner` flag is cosmetic/local only.

### 3.3 Registration: a validated registry of public links

**A submission is `repo@commit`** — a pinned public link that your login owns.
The **commit SHA is the content identity** (it is already a hash of the exact
tree), so there is **no separate `solution_digest`**. The single invariant to
preserve is that the ref is a **full commit SHA, never a branch** — already
enforced by `pull.py` — so "pull it later" (M2) is reproducible.

**`POST /register` becomes cheap and clone-free.** New request shape:

```json
{ "reference": { "repo": "github.com/<login>/<name>", "commit": "<40-hex sha>" },
  "root": "<optional subdir; else read from the repo manifest / default '.'>" }
```

Handler steps (no clone, no code execution, no evidence):

1. `token → login` (`auth.resolve`; real `GitHubAppAuth` on the server).
2. `commit` is a 40-hex sha (reject bare branches/tags).
3. `owns_repo(login, repo)` (`hub/auth.py:owns_repo`).
4. **Commit exists in a readable public repo:** one
   `GET /repos/{owner}/{name}/commits/{sha}` via a new httpx read-client using
   the caller's own token (implicit public-repo read; no hub secret). 404 →
   clear "unknown commit / private repo — make it public; the dev hub is a later
   milestone."
5. *(optional)* the run-manifest (`nethackers.solution.json`) is present at that
   commit — one `contents` API call, to fail fast on a non-runnable link. May be
   deferred to the M2 worker.
6. Store `(repo, commit, root, owner=login, registered_at)`.

**Deleted from the register path:** `LocalStubGit`, the manifest-well-formed
check, the digest-match check, the evidence-well-formed check, and atom
insertion (ladder steps 3–6 of `validate.py`). `validate.py` shrinks to
identity + reference + a *real* commit-existence check via the new read-client.

### 3.4 Data model consequence: boards stay dark until M2

Because registration ingests only the link (no self-reported per-seed results),
**no scores exist in M1.** The score-based read views (`/board`, `/elites`,
`/attainment`, frontier) are computed from atoms and will be **empty until M2's
worker writes held-out results.** M1's live read surface is the **registry**:
`/search` (list registered solutions) and `/solutions/{id}` (show one). This is
correct under the "server is the only scorer" model and keeps the hub honest (no
unverified numbers shown as if they were scores).

**Schema:** `solutions` is keyed by `(repo, commit_sha)` instead of `digest`;
the `digest` column is dropped; `entrypoint` becomes optional (read at eval
time). The `atoms`/`attainment`/`elite` tables are unused in M1 and are
repurposed by M2 for held-out results keyed by the solution identity. Since the
VMs are wipeable and migrations are explicitly deferred (memory
`hub-catalog-change-needs-db-wipe`), schema changes ship as wipe + redeploy — no
migration code.

**Considered and deferred (flip at spec review if you disagree):** M1 could
*optionally* accept self-reported evidence so `/board` is populated immediately
and M2 triage has a promise signal. Rejected for M1 because (a) it shows
unverified numbers, (b) M2's worker can triage off a cheap server-side
first-pass eval instead, and (c) it keeps "just a link" literally true.

### 3.5 Hub deployment

Reuse `-internal`'s hardened `deploy/compose.yaml` + `deploy/Caddyfile`,
**simplified** to what the link-registry needs:

- **Services: `hub` + `caddy` only.** Drop `hub-dev` (the `/dev`
  private-`nethacker-dev` contour needs a GitHub *installation* token — a
  public-launch/M2 feature). Drop the `/opt/autoascend` read-only mount and
  `--baseline` (that served `-internal`'s "reconstruct from root" provenance,
  which §3.3 replaced). Drop `NETHACKERS_CONTRIBUTOR_TOKEN` (that was
  `-internal`'s shared gate-token; v1 authenticates each user by their own
  GitHub token).
- **Keep every hardening line:** `read_only`, `cap_drop: ALL`,
  `no-new-privileges`, `pids_limit`, `mem_limit`, `cpus`, tmpfs `/tmp`, rotated
  json logging, `init: true`. Caddy keeps auto-TLS + HTTP→HTTPS + HTTP/3 +
  security headers, pinned by digest; collapse its two `handle` blocks to one
  `reverse_proxy hub`.
- **The hub container runs v1's app** via the new `nethackers-hub` entrypoint
  wrapping `create_default_app` under uvicorn, configured purely by env:
  `NETHACKERS_DB=/data/hub.sqlite3` (**SQLite WAL**), `NETHACKERS_CLIENT_ID`,
  **no** `STUB_IDENTITIES`, **no** `LOAD_FIXTURES`. Production starts empty; the
  73-identity *catalog* is code-derived (`hub/objectives.py:CATALOG`), always
  present. Add a tiny **`/healthz`**.
- **Secrets on the hub VM in M1: none** (the public `client_id` is all `hub.env`
  carries).
- **Provision** the wiped box with `-internal`'s `scripts/provision.sh` (Docker,
  UFW → SSH/80/443 only, deploy account, swap, SSH hardening); lay out
  `/srv/nethackers/{data,backups}`; nightly SQLite `.backup` to `/backups`.
- **Flip the CLI default** `NETHACKERS_HUB` → `https://nethackers.dunnolab.ai`
  (keep `--hub`/env override for local + self-hosters).
- **Image delivery: CI → GHCR → pull.** A GitHub Actions workflow builds the hub
  image on push/tag and pushes `ghcr.io/dunnolab/nethackers-hub`; the VM pulls a
  **digest-pinned** image (`NETHACKERS_HUB_IMAGE`). Rollback = pin the previous
  digest, `compose up`.

### 3.6 Testing & rollout

**Everything new tests offline** via existing injectable seams (`LocalStubAuth`,
fake `httpx`, injectable `sleep`/`prompt`), in the fast suite
(`pytest -m "not nle and not docker and not claude_live and not codex_live"`):

- Refresh flow: expiry → refresh → atomic re-persist → old-pair invalidation
  (fake http); credentials-format migration (old `{login, token}` reads
  gracefully).
- Register-link path: identity + `owns_repo` + commit-exists (fake GitHub API) +
  optional manifest-present; rejections — bare branch (non-SHA), unowned repo,
  missing commit, private/404 repo. No clone anywhere.
- Auth flip + `/healthz`: `create_default_app` on the `GitHubAppAuth` branch with
  injected http.
- New opt-in **`github_live`** marker (mirrors `claude_live`/`codex_live`): a
  real device-flow + real `/user` against the registered App, run by hand.
- Deploy smoke (docker-gated, extending `tests/test_compose_smoke.py`):
  `compose up` hub+caddy, `curl /healthz` + `/objectives`, confirm a token-less
  register 401s.

**CI:** two workflows — image build/push (above) and a **test gate** running
`make test` + `make check` (fast suite + mypy + ruff) on every PR.

**Rollout (manual-test gate — no push/PR until Vlad has driven it; memory
`manual-test-gate-before-push-pr`):** provision hub VM → CI builds/pushes image
→ VM pulls pinned digest → verify TLS + `/healthz` + `/objectives` → register the
real GitHub App, set `client_id` → full end-to-end: real `nethackers login` →
`register` a real public `repo@commit` → it appears in `/search`.

---

## 4. Security & threat model (trusted now, public-ready later)

- **Identity/attribution:** server-enforced via GitHub token → login; you can
  only register a repo you own. No spoofing.
- **No hub secrets:** nothing on the hub VM to steal in M1.
- **Reproducibility:** full-SHA pinning means the code M2 pulls is exactly what
  was registered.
- **Soft DoS guards:** register does only cheap API calls (no clone); Caddy +
  compose resource caps bound blast radius on the 2 GiB VM.
- **Explicit public-launch gaps (deferred, documented, not papered over):** no
  rate-limiting; single-writer SQLite; public repos only; no abuse handling; no
  `/dev` private-repo contour. Acceptable for a trusted cohort; each is a gate to
  close before opening registration to strangers.

## 5. Deferred to Milestone 2 (the verifier)

Held-out seed key on the evaluator host; the always-on worker (lease → pull
`repo@commit` → run in the hardened arena sandbox on secret held-out seeds → post
results); the queue + lease API; the triage policy (cheap first-pass →
"promising" → held-out); the `held-out` tier and the score-based leaderboards;
`trust.py`-style reputation; private `nethacker-dev` support via GitHub App
installation tokens.

## 6. Open questions for spec review

1. **Boards-dark-until-M2** (§3.4) — confirm link-only, or flip to optionally
   accepting self-reported evidence in M1.
2. **Manifest check at register** (§3.3 step 5) — do it at register for fast
   feedback, or defer entirely to the M2 worker.
3. **`solutions` key** — `(repo, commit_sha)` natural key vs a synthetic id with
   `UNIQUE(repo, commit_sha)`.
