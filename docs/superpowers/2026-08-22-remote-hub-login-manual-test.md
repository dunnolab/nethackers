# Manual test guide — Remote hub + GitHub login (M1)

Branch `brainstorm-github-login-hub` (17 commits, **not pushed**). Automated checks
already pass here: `make test` → 492 passed; `make check` → mypy + ruff clean.
This guide is the human-in-the-loop validation before push/PR.

Spec: `docs/superpowers/specs/2026-08-22-remote-hub-and-github-login-design.md`

---

## 0. The one human step (only you can do this)

Register the **"NetHackers Hub" GitHub App** under `dunnolab` (github.com → org →
Settings → Developer settings → GitHub Apps → New):

- **Repository permissions → Contents: Read-only** (Metadata: Read-only auto-adds). Nothing else.
- **Enable Device Flow: ✓**
- **Expire user authorization tokens: ✓**
- **Webhook → Active: ✗**  ·  Installation: not required.
- Copy the public **Client ID** (looks like `Iv1.xxxx` / `Iv23xxxx`).

Wire it in **one** of two ways:

- **For testing now (no code edit):** `export NETHACKERS_CLIENT_ID=<the-client-id>` in
  every shell (the CLI *and* the hub both read it).
- **For release:** replace the placeholder constant `NETHACKERS_APP_CLIENT_ID` in
  `src/nethackers/hubclient/register.py` (and `deploy/hub.env.example`) with the real id,
  so contributors need no env var.

---

## Track A — offline sanity (no GitHub App needed)

```bash
make test          # fast suite — expect: 492 passed
make check         # mypy + ruff — expect: clean
make up            # builds arena + starts the LOCAL stub hub on :8000, waits for ready
curl -s localhost:8000/healthz     # -> {"status":"ok"}
curl -s localhost:8000/objectives  # -> the 73-identity catalog (code-derived, always present)
make down
```

The local `make up` hub uses **stub auth + fixtures**, so its boards show fixture data —
that is local-dev only. The *production* hub starts empty (real registrations only).

---

## Track B — real login + register (needs the App from step 0)

Run the hub locally **with real auth** (stub off), in one shell:

```bash
NETHACKERS_DB=./hub.sqlite3 NETHACKERS_CLIENT_ID=<client-id> uv run nethackers-hub --port 8000
```

In a second shell, log in and register a real public solution you own:

```bash
export NETHACKERS_HUB=http://localhost:8000
export NETHACKERS_CLIENT_ID=<client-id>

uv run nethackers login
#   -> prints a github.com/login/device URL + an 8-char code; authorize in a browser.
#   -> "logged in as @<you>"; credential saved to ~/.nethackers/credentials.json (chmod 600).

# Push any solution to YOUR public repo first, note the full commit SHA. Then:
uv run nethackers register --repo github.com/<you>/nethacker --commit <full-40-hex-sha>
#   -> {"solution_id": "github.com/<you>/nethacker@<sha>", "owner": "<you>", ...}

uv run nethackers search --owner <you>    # your registered link should appear
```

**Things worth deliberately checking (all should behave):**
- Register a repo you **don't** own → `403` "does not own".
- Register with a branch name instead of a 40-hex SHA → `400` (must be a pinned commit).
- Register a **private**/nonexistent repo → clear "unknown commit / private repo" error (public-only in M1).
- Let the 8h token lapse (or delete `expires_at` in the creds file) and run another command →
  it should **silently refresh** and keep working (login once).

---

## Track C — deploy to the VM (optional now; full runbook in `deploy/README.md`)

Servers are wipeable. Summary:

1. **DNS**: `A` record `nethackers.dunnolab.ai → 45.91.237.200` (must resolve before first `up`, for ACME).
2. **Provision** (as root, once): `sudo deploy/provision.sh "ssh-ed25519 AAAA... deploy"`.
3. **Image**: push a tag to trigger `.github/workflows/hub-image.yml` (builds → `ghcr.io/dunnolab/nethackers-hub`), or build+push manually; pin the **digest**.
4. **Config**: `sudo install -m 0640 -o root -g nethacker deploy/hub.env.example /etc/nethackers/hub.env`, then set the real `NETHACKERS_CLIENT_ID` + the digest-pinned `NETHACKERS_HUB_IMAGE`. **No secrets** beyond the public client id.
5. **Up**: `sudo install -m0644 deploy/Caddyfile /srv/nethackers/Caddyfile && docker compose --env-file /etc/nethackers/hub.env -f deploy/compose.yaml up -d`
6. **Verify**: `curl --fail https://nethackers.dunnolab.ai/healthz` (Caddy auto-provisions TLS).

Then repeat Track B against `NETHACKERS_HUB=https://nethackers.dunnolab.ai`.

---

## M1 behavior changes to review (decisions I made under the design — flag if any is wrong)

1. **Boards/leaderboards are dark (no scores) until the M2 verifier.** By design: the server
   is the only scorer, and it doesn't exist yet. M1's live surface is the registry (`search`, `show`).
2. **The evolve loop no longer auto-publishes wins to the hub.** `register_win` is a no-op:
   the loop still accepts a win as its new *local* elite, but hub registration is now the deliberate,
   manual `nethackers register` step (a synthetic-commit auto-publish can't satisfy link-only
   verification). This is the biggest behavior change — confirm you're OK with it.
3. **Registration is link-only**: your own **public** `repo@commit` (full SHA). No content digest,
   no evidence upload. Private `nethacker-dev` repos are deferred to M2.
4. **The hub container runs as root** inside a locked-down sandbox (all caps dropped,
   no-new-privileges, read-only rootfs). Running it as a dedicated nonroot user is a documented
   hardening follow-up.

Open design questions still parked (see the spec §6): whether to accept optional self-reported
scores in M1, whether to check the manifest at register time, and the `solutions` key naming.

---

## After you're satisfied

Nothing is pushed and no PR exists (your gate). When happy: push `brainstorm-github-login-hub`
and open the PR. Then M2 (the held-out verifier) is the next spec.
