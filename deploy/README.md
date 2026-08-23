# Deploying the NetHackers hub

Operator runbook for the global hub at `https://nethackers.dunnolab.ai`
(VM `45.91.237.200`). Two containers: the FastAPI **hub** (internal only) behind
**Caddy**, which terminates TLS and auto-provisions a Let's Encrypt certificate.

> **No secrets live in this repo.** The only value the hub needs is
> `NETHACKERS_CLIENT_ID`, the GitHub App's **public** client id. There is no client
> secret and no App private key anywhere in this deployment — the hub reads GitHub
> using the *caller's* token.

## Host layout

Everything the deploy touches, after `provision.sh` has run:

```
/srv/nethackers/
├── data/          # hub SQLite DB (hub.sqlite3); root-owned, written by the hub container
├── backups/       # your DB snapshots (root:nethacker, mode 0750)
└── Caddyfile      # copied from deploy/Caddyfile; mounted read-only into caddy
/etc/nethackers/
└── hub.env        # NETHACKERS_CLIENT_ID + NETHACKERS_HUB_IMAGE (mode 0640 root:nethacker)
~nethacker/nethackers/deploy/   # this directory (compose.yaml lives here)
```

Docker volumes `caddy_data` (certs) and `caddy_config` are managed by Compose.

## First-time setup

1. **DNS.** Create an `A` record `nethackers.dunnolab.ai → 45.91.237.200` and let it
   propagate *before* first `up` — Caddy needs the name to resolve to this host to
   complete the ACME HTTP/TLS challenge.

2. **Provision the VM.** As root, once, passing your deploy public key. This installs
   Docker Engine + Compose v2, UFW (opens only SSH / 80 / 443), and
   unattended-upgrades; creates the `nethacker` deploy user; adds 2 GiB swap if none;
   disables SSH password auth; and creates `/srv/nethackers/{data,backups}`.

   ```bash
   sudo ./provision.sh "ssh-ed25519 AAAA... deploy@nethackers"
   ```

   The script is idempotent. Verify you can SSH in as `nethacker` before dropping root
   access.

3. **Get the deploy files onto the host** (as `nethacker`), e.g. clone this repo into
   `~/nethackers`, then place the Caddyfile where Compose expects it:

   ```bash
   sudo install -m 0644 deploy/Caddyfile /srv/nethackers/Caddyfile
   ```

4. **Configure the environment file** (public id + pinned image), mode `0640`,
   owned `root:nethacker`:

   ```bash
   sudo install -m 0640 -o root -g nethacker deploy/hub.env.example /etc/nethackers/hub.env
   sudo -e /etc/nethackers/hub.env   # set NETHACKERS_CLIENT_ID + a digest-pinned NETHACKERS_HUB_IMAGE
   ```

   Pin the image by **digest** (`ghcr.io/dunnolab/nethackers-hub@sha256:...`), not a
   moving tag, so redeploys and rollbacks are deterministic.

5. **Start it** from `deploy/`:

   ```bash
   docker compose --env-file /etc/nethackers/hub.env -f compose.yaml up -d
   ```

## Verify

```bash
docker compose --env-file /etc/nethackers/hub.env -f compose.yaml ps   # hub healthy, caddy up
curl --fail https://nethackers.dunnolab.ai/healthz                      # -> {"status":"ok"}
```

The hub is never exposed directly; reach it only through Caddy on 443. Caddy also
serves plain `:80` and redirects it to HTTPS.

## Upgrade / rollback

Both are the same one-line change to `NETHACKERS_HUB_IMAGE` in
`/etc/nethackers/hub.env`, then re-up:

```bash
sudo -e /etc/nethackers/hub.env    # set NETHACKERS_HUB_IMAGE to the new (or previous) @sha256 digest
docker compose --env-file /etc/nethackers/hub.env -f compose.yaml up -d
```

**Rollback = pin the previous `NETHACKERS_HUB_IMAGE` digest** and re-up. Because the
DB lives on the host at `/srv/nethackers/data`, it survives container replacement; take
a snapshot into `/srv/nethackers/backups/` before a risky upgrade.

## Notes

- The hub container runs read-only with all Linux capabilities dropped, no new
  privileges, and tight CPU/memory/pids limits (see `compose.yaml`). Its only writable
  paths are the `/data` volume and a small `tmpfs` at `/tmp`.
- The hub image runs as **root** inside that locked-down container (all caps dropped,
  no-new-privileges, read-only rootfs), so `data/`/`backups/` stay root-owned and the
  SQLite DB is writable with no chown dance. Running the hub as a dedicated nonroot user
  is a hardening follow-up (see the design spec's public-launch gaps); if you switch,
  chown those directories to that uid.
- Self-hosting is unsupported: you host it, you own it.
