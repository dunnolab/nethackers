# Hub Deploy Mechanism: release-tag CD, public code / private infrastructure

- **Date:** 2026-08-27
- **Status:** Design — approved in brainstorm, pending spec review.
- **Author:** Vlad + Claude (brainstorm session)
- **Topic:** Replace the hub's tribal-knowledge, trap-laden deploy with one
  documented, agent-runnable mechanism: a `v*` release tag builds a private
  image in CI and a hands-off CD job pulls it onto the VM by digest, with
  health-checked auto-rollback — under a governing rule that the **code is
  public but the running infrastructure stays private**.
- **Supersedes:** the deploy-delivery paragraph of
  `2026-08-22-remote-hub-and-github-login-design.md` §3.5 (which named the shape
  — "CI → GHCR → digest-pinned pull" — but was never fully wired).

---

## 1. Why this exists

The hub at `https://nethackers.dunnolab.ai` (VM `45.91.237.200`) has **two
divergent deploy stories**, and only tribal knowledge bridges them.

- **What the repo documents:** `deploy/README.md` + `deploy/compose.yaml`
  describe a clean registry flow — pin `NETHACKERS_HUB_IMAGE` to a
  `ghcr.io/dunnolab/nethackers-hub@sha256:…` digest, `docker compose up -d`,
  rollback by pinning the previous digest.
- **What actually happens:** the image is built **on the 2 GiB VM** (`git
  archive` → scp → `docker build`), tagged locally, flipped with
  `--force-recreate`, dragging in "three traps" (Caddy `depends_on` hang, the
  box swap-thrashing itself network-unreachable for 5–7 min mid-build, and a
  health check that false-passes on the still-running old container).

**Root cause of the drift, in three parts:**

1. **The private registry had no VM pull path.** CI builds and pushes the image
   to GHCR successfully on every `v*` tag (verified: v0.8.1 → v0.12.0 all built
   in ~50 s), but the package is **private** and the VM has no credentials, so
   an anonymous `docker pull` fails — forcing the on-VM build.
2. **CI only builds on `v*` tags**, while deploys happened on arbitrary
   mid-cycle commits (landing-copy fixes, the poll feature) that were never
   tagged — so even a reachable registry would have had no image for them.
3. **Nothing automated the flip.** The "pull by digest" step was a manual
   `docker compose` line a human ran, which is where all the trap knowledge
   accumulated.

Ironically, the *overly-strict-but-unusable* private registry pushed the team
toward a **more** exposed workaround: source archives and a build toolchain
running on the production box, with deploys hand-driven as **root** over a
world-open SSH port. The private-infra posture was defeated by its own friction.

**Goal:** one mechanism that is (a) **documented** and true to reality, (b)
**agent-friendly** — any agent or human runs one thing, no tribal knowledge, no
traps — and (c) faithful to the governing principle below.

### The governing principle

> **We develop fully in public, but the infrastructure we run must stay private
> and not be accessible by everybody.**

This is a *security* boundary (withhold operational access), not a *business*
one (withhold code). It puts the project at **Tier 1** of the open-source-hosted
pattern — a fully-open application with private operations only, like Discourse,
Mastodon, or Ghost — which needs **no private companion repo and no
feature-gating**, only discipline about the operational boundary. The litmus
test, verbatim from the Twelve-Factor App:

> *"[The test is] whether the codebase could be made open source at any moment,
> without compromising any credentials."* — [12factor.net/config](https://12factor.net/config)

Today that test already passes at the code layer (no committed secrets); the
leaks are all operational, and this spec closes them.

---

## 2. Decisions locked in the brainstorm

| Axis | Decision |
|---|---|
| Image delivery | CI builds on `v*` tag → **private** GHCR (`@sha256` digest) → VM pulls by digest. No on-VM builds, ever. |
| Trigger | **Release tag `v*` only.** Every prod deploy is a versioned release; one tag fans out to PyPI publish + hub image build + hub deploy. |
| Automation | **Push-to-deploy CD** (GitHub Actions). Hands-off after `git push --tags`. |
| Safety net | The deploy job health-checks the new container *and* the live URL; **auto-rollback** to the previous digest on any failure. |
| Registry visibility | **Private.** The running image is production infrastructure; keep it a controlled artifact. |
| VM pull auth | **Ephemeral, job-scoped `GITHUB_TOKEN`** injected over SSH for the one pull — **no standing registry credential on the box.** |
| Transport | SSH as the non-root **`nethacker`** user, key locked to a **forced command.** |
| Network | VM joins a **Tailscale** tailnet; public port **22 is closed**; CI reaches the box via an **ephemeral Tailscale node.** |
| Public-repo guardrails | `production` GitHub **Environment** (tag-pattern `v*`, environment-scoped secrets), **protected-tag ruleset**, read-only default `GITHUB_TOKEN`, third-party actions **pinned to commit SHA**, CI stays on `pull_request` (never `pull_request_target`). |
| Deploy logic location | **`deploy/deploy-hub.sh` in the repo.** The CD job and a human/agent invoke the *same* script — automation and agent-friendliness are the same artifact. |

---

## 3. Current state → gaps (where the boundary breaks today)

App-layer analysis is reassuring — the leaks are all in the infrastructure
plane, which is exactly where the principle bites.

| Boundary | Today | Verdict | Fix in this spec |
|---|---|---|---|
| Secrets in repo | none; `.env`/`*.env` gitignored, `*.env.example` tracked, seed key host-only | ✅ holds | keep; add the litmus test to the deploy README |
| Hub network exposure | `expose: 8000` only, behind Caddy — **not** published to the host | ✅ holds | document the landmine: never add `ports: 8000:8000` (§4.6) |
| Control-plane endpoints | none on the public HTTP surface; `/register` is auth-gated; ops are all out-of-band | ✅ holds | keep; if ever added, bind `127.0.0.1` / separate process |
| Registry | private GHCR package | ⚠️ no VM pull path → build-on-VM drift | keep private; ephemeral-token digest pull (§4.3) |
| SSH | **port 22 open to the world**; key-only; **deploy runs as root** | ❌ breaks | forced-command key as `nethacker`; close `:22` via Tailscale (§4.6–4.7) |
| CI deploy | image builds on `v*` (works); nothing flips it; no public-repo guardrails | ❌ incomplete | tag-only deploy job + guardrails (§4.5, §4.8) |

The two ✅-holds we must not regress are load-bearing and cheap to keep, so they
are written into the design as explicit invariants, not assumptions.

---

## 4. Design

### 4.1 The mechanism, end to end

One action — **`git tag vX.Y.Z && git push --tags`** — is the single production
lever:

```
git push --tags vX.Y.Z
   ├─ publish-pypi.yml   (exists)  → PyPI
   └─ hub-image.yml
        ├─ build-push    (exists)  → ghcr.io/dunnolab/nethackers-hub  (private; @sha256 + vX.Y.Z tags)
        └─ deploy  (NEW, needs: build-push, environment: production)
             1. join tailnet (ephemeral node)   ── reach the box privately
             2. ssh nethacker@vm  (forced command)   ── pipe ephemeral GHCR token on stdin
                 → deploy-hub.sh deploy <@sha256 digest>
                     a. docker login ghcr.io (ephemeral token) → pull @sha256 → logout
                     b. pre-flip boot check on a throwaway port  ── image starts?
                     c. record current digest → deploy-history.log
                     d. write hub.env; docker compose up -d --no-deps hub
                     e. health-check the NEW container's image id
                     f. curl --fail https://nethackers.dunnolab.ai/healthz
                     g. any failure after (d) → restore prev digest, re-up, exit non-zero
```

Prod is therefore always a **named release pinned to a digest**, recorded on the
box. No bare commits, no local tags, no on-VM builds.

### 4.2 Public code, private infrastructure

The principle (§1) resolves into three rules the design holds to:

1. **Commit the template, gitignore the secret.** Already true (`*.env.example`
   tracked, `.env`/`*.env` ignored, the held-out HMAC seed key host-only on the
   separate evaluator box). Every value the hub needs at runtime is either
   public (the GitHub App `client_id`) or injected on the VM, never in the tree.
2. **Immutable build → release → run; the tag is the release ID.**
   ([12factor.net/build-release-run](https://12factor.net/build-release-run).)
   `vX.Y.Z` → an immutable image (build) + the VM's private config (release) →
   the running container (run). A release is never mutated in place.
3. **Private ≠ secret-safe.** Registry privacy is *distribution and provenance
   control*, never a place to hide secrets — image layers are trivially
   inspectable (`docker history`, `dive`) by anyone who can pull. Nothing
   sensitive is ever baked into the image; secrecy lives only in host-only env
   and the keyed-HMAC "publish the format, not the secret" design.

**What stays out of the repo vs. injected at deploy:**

| Item | In the public repo? | Handled at deploy how |
|---|---|---|
| Secrets (deploy SSH key, Tailscale creds) | never — placeholders only | GitHub **environment** secrets → used only inside the gated deploy job |
| Env values (DB path, client id) | keys/format documented; `*.env.example` | `hub.env` on the VM (12-factor config) |
| TLS certs / keys | never | Caddy obtains + renews via ACME at runtime; certs live only on the VM ([Caddy auto-HTTPS](https://caddyserver.com/docs/automatic-https)) |
| Infra topology (IPs, firewall, tailnet) | private ops note only | not in the tree |
| Container image | built by CI | **private** GHCR package, pulled by digest (§4.3) |

Why keep the *image* private when the source is public (it is reproducible, so
privacy buys no code secrecy): it keeps a deliberate control point in the deploy
chain — we do not ship a free, prebuilt artifact for bulk scanning, and only our
CI publishes the exact digest we run. That is the honest, principle-aligned
reason, and the cost (a host credential) is eliminated by §4.3.

### 4.3 Image delivery: private GHCR, ephemeral-token digest pull

`build-push` already builds and pushes on `v*` (keep it; add a job `outputs`
mapping so the deploy job receives `steps.build.outputs.digest`). The new part
is how the VM pulls a **private** image without holding a standing credential:

- The deploy job has `permissions: packages: read`, so its short-lived
  `GITHUB_TOKEN` (expires at job end) can read the private package.
- The job pipes that token to the VM **on stdin** (never in argv or
  `$SSH_ORIGINAL_COMMAND`, which can be logged). On the VM, `deploy-hub.sh`:
  ```sh
  trap 'docker logout ghcr.io >/dev/null 2>&1' EXIT
  docker login ghcr.io -u "$GH_ACTOR" --password-stdin <<<"$token"
  docker pull "ghcr.io/dunnolab/nethackers-hub@sha256:$digest"
  ```
- **No registry credential ever persists on the box.** The pull is
  layer-deduped, so only the changed `src/` layer moves (light on 2 GiB), and it
  is **digest-pinned**, so "flip to the new image" is deterministic.

Deploy always references `@sha256:…`, never a moving tag.

*Fallback (documented, not chosen):* `docker save | ssh | docker load` gives the
VM zero registry reachability, but ships the whole image every deploy and pins
to "the tar CI built" rather than a published manifest digest — worse on both
bandwidth and determinism, so it is the fallback, not the default.

### 4.4 The deploy script — the agent-friendly centerpiece

`deploy/deploy-hub.sh` is the single source of deploy truth: reviewed, testable,
and run identically by CI and by a human/agent. It encodes every trap-avoidance
as an *enforced step*, not a remembered one.

**Subcommands** (dispatched from `$SSH_ORIGINAL_COMMAND` under the forced-command
key, or run directly on the box):

- `deploy <@sha256 digest>` — reads the ephemeral token on stdin, then:
  1. `docker login` + `pull` the digest (§4.3); `logout` on exit (trap).
  2. **Pre-flip boot check** — run the pulled image on a throwaway container on
     an ephemeral port against a scratch DB, `curl /healthz`, tear it down.
     Catches "image won't start" *before* touching the live container.
  3. Append the current digest → `/srv/nethackers/deploy-history.log`
     (timestamp, tag, digest, actor) and stash it as the rollback target.
  4. Write the new digest to `/etc/nethackers/hub.env`; then
     `docker compose --env-file … up -d --no-deps hub`.
     **`--no-deps` dodges the Caddy `depends_on: service_healthy` hang.**
  5. **Post-flip health check on the *new* container's image id** (not "is some
     hub healthy") — dodges the false-pass-on-the-old-container trap.
  6. **Live-URL check** — `curl --fail https://nethackers.dunnolab.ai/healthz`.
  7. **Auto-rollback** — any failure at/after step 4 restores the previous
     digest, re-ups, re-verifies, and exits non-zero so CI marks the run failed.
- `rollback` — re-pin the previous digest from the history log, re-up, verify.
- `status` — print the running image digest, container health, live-URL status,
  and the last N deploy-history lines.

**Flags:** `--yes` (non-interactive, for CI), `--dry-run` (an agent can preview
every step without touching prod), `--help` (self-documenting). Interactive runs
confirm before the flip.

This is what makes the mechanism agent-friendly *and* automated at once: the
emergency command a human or agent runs by hand — over the tailnet — is byte-for-
byte what CI runs.

### 4.5 The CD workflow

Add a `deploy` job to `hub-image.yml` (both build and deploy are `v*`-triggered,
so one workflow is cleanest):

```yaml
# hub-image.yml
on:
  push: { tags: ['v*'] }         # deploy fires ONLY on tag push
permissions:
  contents: read                  # least privilege; deploy adds packages: read
concurrency:
  group: production-deploy
  cancel-in-progress: false       # never abort a half-finished rollout
jobs:
  build-push:  { ... , outputs: { digest: ${{ steps.build.outputs.digest }} } }
  deploy:
    needs: build-push
    environment: production        # the who/when gate (§4.8)
    permissions: { contents: read, packages: read }
    steps:
      - uses: actions/checkout@<full-sha>          # pin to SHA, not @v4
      - uses: tailscale/github-action@<full-sha>   # ephemeral tailnet node
        with: { oauth-client-id: ..., oauth-secret: ..., tags: tag:ci, ephemeral: true }
      - run: |
          echo "${{ secrets... }}" > key ; chmod 600 key
          printf '%s' "$GITHUB_TOKEN" | ssh -i key nethacker@<tailnet-host> \
            "deploy ghcr.io/dunnolab/nethackers-hub@${{ needs.build-push.outputs.digest }}"
```

Public-repo hardening baked in (see §4.8 for the settings side):

- **Never `pull_request_target`.** CI (`ci.yml`) stays on `pull_request` —
  de-privileged, no secrets — so fork PRs can never reach deploy credentials.
  The "pwn request" (running fork code with your secrets) is the #1 public-repo
  footgun ([GitHub Security Lab](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/)).
- **Deploy triggers only on tag push** — outside contributors can't push tags to
  the repo, so they cannot fire it at all.
- **Never interpolate `${{ github.event.* }}` into a `run:` block** (script
  injection); pass through `env:` and quote.
- **Pin every third-party action to a full commit SHA** — the only way to use an
  action as an immutable release.

### 4.6 Network: a private box via Tailscale

**The bind this solves:** we want the box private, but CI must reach it, and
GitHub-hosted runners have dynamic, shared IPs that GitHub **explicitly says not
to allowlist** ([about GitHub's IP addresses](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses)).
So "just allowlist CI" is not an option.

**Tailscale** is a private network overlay built on WireGuard. Each machine you
enroll (the VM, your laptop, a CI runner) joins a private *tailnet* and gets a
stable `100.x` address; they reach each other over direct, end-to-end-encrypted
links. A coordination server brokers public keys and addresses but **never sees
traffic** (the encryption keys never leave the devices).

**What we do with it:**

- Enroll the VM in the tailnet at provision time (one-time auth key).
- **Close public port 22 entirely.** UFW allows `:22` only from the tailnet
  (deny from the internet); `:80`/`:443` stay public (that's the website).
  Result: an internet scan finds **no SSH port** — nothing to brute-force, and a
  future OpenSSH CVE cannot be reached from outside the tailnet.
- CI joins via an **ephemeral node** (`tailscale/github-action`, `ephemeral:
  true`, a scoped OAuth client + `tag:ci`): the runner joins the tailnet
  just-in-time, SSHes to the VM over the private link, then **auto-removes
  itself** when the job ends. No standing CI machine, and `:22` stays closed to
  the world.
- **ACLs** restrict the CI tag to exactly `nethacker@vm:22` and nothing else.

**The Docker/UFW landmine (invariant, do not step on it):** Docker publishes
ports to `0.0.0.0` and inserts its iptables rules **ahead of UFW**, so a
published container port is internet-open *even with `ufw deny`*
([Docker packet filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).
The hub is safe today because it uses `expose:` (compose-network only), not
`ports:`. **Never add `ports: 8000:8000` to the hub service** — it would bypass
the firewall entirely. Only Caddy publishes ports, deliberately.

**Honest cost:** Tailscale is a new standing dependency (account, clients, an
ACL policy, a scoped key in CI) and trusts their control plane for
*authorization* (not for reading traffic). For a single VM and a trusted cohort
this is proportional to the principle; if we ever want to drop even the
control-plane trust, the self-hostable coordinator **Headscale** is the
documented upgrade (more work; out of scope now).

### 4.7 SSH & host hardening

- **Forced-command deploy key** — the highest-leverage single move. The
  `nethacker` deploy key in `authorized_keys` is prefixed:
  ```
  restrict,command="/usr/local/bin/deploy-hub.sh" ssh-ed25519 AAAA... deploy-ci@github
  ```
  Even a stolen key can only run the deploy script; the requested subcommand +
  digest arrive in `$SSH_ORIGINAL_COMMAND` for the script to validate, and
  `restrict` disables forwarding/PTY ([sshd(8)](https://man.openbsd.org/sshd.8)).
- **Non-root deploy user** (`nethacker`, already created by `provision.sh`), not
  root. **Honest caveat:** docker-group membership is root-equivalent
  ([Docker post-install](https://docs.docker.com/engine/install/linux-postinstall/)),
  so the non-root user is not itself the privilege boundary — **the
  forced-command script is.** Rootless Docker is the documented hardening upgrade
  (out of scope now).
- **Dedicated, rotatable ed25519 key** for CI, separate from human admin keys;
  revoke = delete one `authorized_keys` line. Human admins use their own keys,
  also reachable only over the tailnet.
- **fail2ban/sshguard** — optional now that `:22` is closed to the internet;
  keep for log hygiene if desired (low value once the port is tailnet-only).

### 4.8 Repo / GitHub configuration (operator checklist, one-time)

These are click-ops settings, documented in `deploy/README.md`:

- **`production` Environment**: deployment restricted to **tag pattern `v*`**;
  the SSH key + Tailscale OAuth creds stored as **environment-scoped secrets**
  (invisible to every non-deploy job); **required reviewers left OFF** to honor
  the hands-off decision — a one-toggle add later if desired
  ([Environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)).
- **Protected-tag ruleset** on `v*` — *Restrict creations*, bypass list =
  release owners only. Controls *who* tags; the Environment controls *which ref*
  deploys. Belt and suspenders.
- **Default `GITHUB_TOKEN` → read-only** (Settings → Actions), so any permission
  a workflow doesn't request is `none`.
- **Require approval for outside contributors' workflow runs.**

### 4.9 Secrets inventory

| Secret | Where it lives | Scope / lifetime |
|---|---|---|
| Deploy SSH private key | `production` environment secret | reaches only the gated deploy job; forced-command-restricted; rotatable |
| Tailscale OAuth client id/secret | `production` environment secret | scoped to `tag:ci`; mints ephemeral nodes only |
| GHCR pull token | **none stored** | ephemeral `GITHUB_TOKEN`, job-lifetime, piped on stdin |
| VM tailnet enrollment key | used once at provision time | not a CI secret |
| `NETHACKERS_CLIENT_ID` | `hub.env` on the VM | **public** GitHub App client id (not a secret) |

Nothing here is committed to the repo; the litmus test (§1) still passes.

---

## 5. Testing

- **Unit** (`deploy-hub.sh` behind an injected `run`/`ssh`/`docker`, mirroring
  `src/nethackers/hubclient/publish.py`'s injected-shell pattern):
  `$SSH_ORIGINAL_COMMAND`
  parsing + digest validation (reject non-`@sha256`), the pre-flip boot check,
  and the auto-rollback trigger (a forced post-flip failure restores the prev
  digest). No live prod anywhere.
- **`--dry-run`** as an executable spec an agent can run to preview.
- **Deploy smoke** (docker-gated, extending `tests/test_compose_smoke.py`):
  `up --no-deps hub` + `curl /healthz` + `/objectives`, and a token-less
  `/register` 401.
- **First real tag deploy is the attended integration test** (§6).

## 6. Transition / rollout

Ordered, and the first live flip is attended even though the mechanism is
hands-off — belt-and-suspenders on the cutover from the on-VM image to a GHCR
digest (same Dockerfile, so it should be clean).

1. **Provision:** install Tailscale on the VM + enroll; close `:22` to the
   internet (UFW: tailnet-only); install `deploy-hub.sh` at
   `/usr/local/bin/`; add the `nethacker` forced-command key.
2. **Repo:** create the `production` environment + protected-tag ruleset; set the
   default token read-only; add the `deploy` job; pin all actions to SHA.
3. **First deploy (attended):** cut `v0.12.1`, watch the Actions run flip prod
   from the current on-VM local image to the GHCR digest; verify `/healthz`, the
   live URL, and a clean board.
4. **Retire the drift:** rewrite `deploy/README.md` to the true mechanism (+ the
   litmus test, the Docker/UFW invariant, the Tailscale + rollback runbook, and
   a short **agent runbook**: "to deploy, tag a release; to emergency
   deploy/rollback, SSH over the tailnet and run `deploy-hub.sh`; never build on
   the VM"). Update the `hub-deploy-mechanism` memory so future sessions stop
   reaching for the three traps. Keep the last on-VM image as a rollback target
   for one cycle, then prune.

## 7. Security & threat model (public repo, private infra)

- **Identity of who can deploy:** protected-tag ruleset (who tags) + `production`
  environment tag-pattern (which ref deploys) + environment-scoped secrets
  (unreadable outside the gated job). Fork PRs are de-privileged (`pull_request`,
  no secrets) and cannot push tags.
- **Reachability of the box:** public `:22` closed; SSH reachable only from the
  tailnet; the CI node is ephemeral and ACL-scoped to `nethacker@vm:22`.
- **Standing credentials on the box:** none for the registry (ephemeral token);
  the deploy key is forced-command-restricted and rotatable.
- **Honest residual gaps (documented, not papered over):**
  - **docker-group is root-equivalent**, so the real privilege boundary is the
    forced-command script's correctness — it is load-bearing and must validate
    its `$SSH_ORIGINAL_COMMAND` arguments strictly. Rootless Docker is the future
    upgrade.
  - **Single VM, single-writer SQLite** — no HA; a bad deploy is caught by
    auto-rollback, not by a standby.
  - **Tailscale control-plane trust** for authorization (not for traffic);
    Headscale is the future self-host option.
  - **`/poll/vote` is an unauthenticated public write** (anonymized, validated,
    one-per-browser) with no rate limit — pre-existing, unchanged here, and
    already listed as a public-launch gap in the M1 spec.

## 8. Deferred / out of scope (YAGNI)

- **No blue-green / zero-downtime.** The hub is single-instance; a few-second
  Caddy 502 during the flip is accepted. Revisit only if uptime SLAs appear.
- **No staging environment** (one VM). The pre-flip boot check is the stand-in.
- **No secret manager** (there are ≈no runtime secrets).
- **Ephemeral SSH certificates** (Vault SSH / Teleport / Tailscale SSH), **rootless
  Docker**, and **Headscale** are named hardening upgrades, not starting points.
- **The M2 evaluator host** (`72.56.24.170`, seed key) is untouched.
- This is a **hub deploy mechanism**, not a general multi-service deploy tool.

## 9. Open questions for spec review

1. **Required reviewer on `production`** — keep OFF (hands-off, as chosen), or
   turn ON for a one-click approval before each prod flip?
2. **fail2ban** — keep it once `:22` is tailnet-only (log hygiene), or drop it as
   redundant?
3. **Workflow shape** — deploy as a second job in `hub-image.yml` (chosen), or a
   separate `deploy.yml` triggered on the same tag?
4. **Pre-flip boot check depth** — throwaway-DB boot check (chosen), or trust the
   post-flip health check + auto-rollback alone for simplicity?

---

## References

- Twelve-Factor App — [Config](https://12factor.net/config),
  [Build/Release/Run](https://12factor.net/build-release-run)
- GitHub Security Lab — [Preventing pwn requests](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/),
  [Untrusted input](https://securitylab.github.com/resources/github-actions-untrusted-input/)
- GitHub Docs — [Deployments & environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments),
  [Securely using `pull_request_target`](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target),
  [About GitHub's IP addresses](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses),
  [GHCR access & visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [Docker post-install (docker group = root)](https://docs.docker.com/engine/install/linux-postinstall/),
  [Docker packet filtering & firewalls (iptables vs UFW)](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
- [sshd(8) — forced commands / `restrict`](https://man.openbsd.org/sshd.8)
- [Tailscale GitHub Action](https://tailscale.com/kb/1276/tailscale-github-action)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [OWASP WSTG — admin interfaces](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/05-Enumerate_Infrastructure_and_Application_Admin_Interfaces.html)
