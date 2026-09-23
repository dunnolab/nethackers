# Deploying the hub

The hub at `https://nethackers.dunnolab.ai` is one VM (`45.91.237.200`,
tailnet name `nethackers-hub`) running two containers: the FastAPI hub,
internal only, behind Caddy, which terminates TLS and keeps its own Let's
Encrypt certificate. Prod always runs a named release pinned to an image
digest, and every change goes through `deploy/deploy-hub.sh`: CI runs it on a
tag push, a human or an agent runs it over the tailnet. Nothing is built on
the VM. We host it; nobody else does. Last reviewed 2026-09-23, at v0.36.2.

## Deploy

Runs as CI, on a `v*` tag.

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

Watch the `Hub image` workflow. `build-push` builds `hub/Dockerfile` and
pushes it to the private `ghcr.io/dunnolab/nethackers-hub` package, tagged
by git commit (`sha-<short sha>`) and by version, never `latest`; the deploy
uses the digest the build step outputs. `deploy` joins the tailnet as an
ephemeral node, SSHes to the VM as `nethacker`, and sends
`deploy ghcr.io/dunnolab/nethackers-hub@sha256:<digest> --yes` with the
job's `GITHUB_TOKEN` on stdin; the CI key's forced command runs it as
`deploy-hub.sh`. The script pulls the digest, boot-checks it on a throwaway
port (up to 30 s), flips with `docker compose up -d --no-deps hub`,
health-checks the new container (up to 60 s), curls the live URL, and rolls
back to the previous digest if any of that fails. Expect a blip of a few
seconds. Deploys serialize and never cancel each other. Every deploy that
passes the boot-check is appended to `/srv/nethackers/deploy-history.log`
before the flip; an automatic rollback adds no line, so after one the last
line names the digest that failed. A `workflow_dispatch` run builds and
pushes an image without deploying it.

> [!NOTE]
> `[skip hub-deploy]` in the tagged commit's message skips the deploy job.
> The check matches the whole message, and GitHub leaves the message field
> empty on some tag pushes, in which case the job deploys anyway: it fails
> open, and a needless deploy is a verified blip. Confirm with `status`.

Verify: `deploy-hub.sh status` shows the new digest and `healthy`.

## Status

```bash
ssh nethacker@nethackers-hub deploy-hub.sh status
curl --fail https://nethackers.dunnolab.ai/healthz    # {"status":"ok","auth":"github"}
```

`status` prints the pinned image ref (read from `hub.env`), container
health, the live URL's status, and the last five deploy-history lines.

## Roll back

Runs as a human or an agent, over the tailnet.

```bash
ssh nethacker@nethackers-hub deploy-hub.sh rollback
```

Re-pins `NETHACKERS_HUB_IMAGE` to the digest recorded as "previous" in the
last history line, one step back from what runs now, and re-ups with
`--no-deps`, so Caddy is untouched. It does not pull: the previous image is
already on the box, so this works with GHCR down. A deploy that fails its
own health or URL check rolls back by itself; reach for this when a deploy
passed those checks and is wrong in a way they can't see, such as a
regression that still answers 200. Run it once: a rollback writes its own
history line, so a second `rollback` re-pins the digest you just left.

Verify: `status`, then the live site.

## Deploy by hand

Emergencies only; a normal deploy is a tag.

```bash
printf '%s' "<ghcr token with read:packages>" | \
  ssh nethacker@nethackers-hub deploy-hub.sh deploy \
  ghcr.io/dunnolab/nethackers-hub@sha256:<digest> --yes
```

`--dry-run` prints the plan and changes nothing; it still reads stdin first,
so pipe something in. `--yes` is accepted for symmetry with CI; the script
never prompts. Never `docker build`, `git archive | ssh`, or hand-edit
`/etc/nethackers/hub.env` and re-up: `deploy` and `rollback` are the only
ways the running hub changes. A change to `deploy-hub.sh` itself is not
deployed by CI: install it by hand,
`sudo install -m 0755 deploy/deploy-hub.sh /usr/local/bin/deploy-hub.sh`.

## Back up and restore

There is no working automated backup. The database is one SQLite file in
WAL mode at `/srv/nethackers/data/hub.sqlite3`, so a plain `cp` while the
hub runs is not a consistent copy, and the `.bak-*` files next to it were
taken that way. A script at `/srv/nethackers/backup.sh` on the VM (not in
this repository) runs SQLite's online backup inside the hub container into
`/srv/nethackers/backups/` and prunes copies older than 14 days, but
`compose.yaml` does not mount that directory (the repository copy never
has), nothing schedules the script, and its newest copy is from 2026-08-02.
A working backup means adding that mount and scheduling the script.

Until then, a consistent copy by hand is the same call the script makes,
into the mounted `/data`:

```bash
ssh nethacker@nethackers-hub 'docker compose --env-file /etc/nethackers/hub.env -f /srv/nethackers/compose.yaml \
  exec -T hub python -c "import sqlite3, datetime
src = sqlite3.connect(\"/data/hub.sqlite3\"); dst = sqlite3.connect(\"/data/hub-%s.sqlite3\" % datetime.date.today())
src.backup(dst); dst.close(); src.close()"'
```

The copy lands next to the live file, root-owned; move it off the box. The
hub image has Python and no `sqlite3` binary, and the host has no `sqlite3`
either. There is no restore procedure. Restoring means stopping the hub,
replacing the file and removing any stale `-wal` and `-shm` beside it, then
re-upping the same image:
`docker compose --env-file /etc/nethackers/hub.env -f /srv/nethackers/compose.yaml up -d --no-deps hub`
(`rollback` would also change the image). It has not been exercised. Also
worth a copy: `/etc/nethackers/hub.env`, the Caddyfile, and the
`caddy_data` volume (certificates; Caddy re-issues them if lost).

## Upgrade with a schema change

The hub migrates its schema at startup, in every worker
(`src/nethackers/hub/store.py`, `init_schema`). Rolling back to an image
older than a migration is untested. Before a release that changes the
schema, take a backup, and treat `rollback` as a last resort.

## Rotate keys

| Key | Lives in | Rotate by |
|---|---|---|
| the human SSH key | `/home/nethacker/.ssh/authorized_keys` on the VM | edit the file over the tailnet from a machine that still has access |
| the CI deploy key | the same file, on a line prefixed `restrict,command="/usr/local/bin/deploy-hub.sh"` | replace the line; the private half is a GitHub environment secret |
| the Tailscale OAuth client (`tag:ci`) | the GitHub `production` environment | issue a new client in the Tailscale admin console, update the secret |
| the VM's tailnet address | the GitHub `production` environment, `HUB_TAILNET_HOST` | update it if the VM is re-enrolled |
| the GHCR pull token | minted per CI job | nothing to rotate |

Root login with a key over the tailnet stays open (`PermitRootLogin
prohibit-password`); the runbook only ever uses `nethacker`.

## Troubleshooting

| You see | Do |
|---|---|
| `status` says unhealthy after a deploy | read `docker compose --env-file /etc/nethackers/hub.env -f /srv/nethackers/compose.yaml logs hub` first, then `deploy-hub.sh rollback`: the rollback recreates the container and discards its logs |
| no certificate, or the site serves Caddy's default page | the DNS `A` record must resolve to this host before Caddy's first start; fix DNS, then `docker compose --env-file /etc/nethackers/hub.env -f /srv/nethackers/compose.yaml restart caddy` |
| the deploy job cannot reach the VM | the Tailscale OAuth client, `HUB_TAILNET_HOST` or the CI key expired or was rotated; check the `production` environment secrets |

## Secrets

- Public: `NETHACKERS_CLIENT_ID`, the GitHub App's client id. There is no
  client secret and no App private key; the hub reads GitHub with the
  caller's own token.
- On the VM only, in `/etc/nethackers/hub.env` (mode `0640`,
  `nethacker:nethacker`): `NETHACKERS_HUB_IMAGE`, and the private tier's
  `NETHACKERS_HIDDEN_SECRET`, `NETHACKERS_HIDDEN_SEEDS` and
  `NETHACKERS_VERIFIER_TOKENS`. `hub.env.example` lists only
  `NETHACKERS_CLIENT_ID` and `NETHACKERS_HUB_IMAGE`; the rest are optional
  and, when set, live in the same file.
- In GitHub environment secrets: the CI deploy key, the Tailscale OAuth
  credentials, and the VM's tailnet address (`HUB_TAILNET_HOST`).

Nothing in the repository is a secret; `.env` and `*.env` are gitignored.

## Provision a new VM

Once per machine. Read all five steps first.

1. DNS: an `A` record `nethackers.dunnolab.ai → <ip>`, propagated. Caddy
   needs the name to resolve here to pass the ACME challenge.
2. From a checkout, with `deploy-hub.sh` next to `provision.sh`:

   ```bash
   sudo TS_AUTHKEY=<one-time tailnet auth key> ./provision.sh \
     "ssh-ed25519 AAAA… <label>" "ssh-ed25519 AAAA… ci"
   ```

   The human key must carry a comment after its body, or the script exits
   with its usage line. It installs Docker Engine and Compose v2, UFW,
   unattended-upgrades and Tailscale; enrols the box as `nethackers-hub`;
   creates the `nethacker` user with the two keys; adds 2 GiB of swap if
   none; disables SSH password auth; sets UFW to deny incoming, with 22
   only on `tailscale0` and 80, 443 and 443/udp public. Idempotent; unset
   `TS_AUTHKEY` on a re-run, since a consumed one-time key fails.

   > [!WARNING]
   > The script closes public port 22. Run it from the provider's console,
   > not an internet SSH session, with `TS_AUTHKEY` set, and confirm
   > `ssh nethacker@nethackers-hub` works over the tailnet before you drop
   > console access. There is no other way back in from the internet.

3. Install the compose file and the Caddyfile where the script expects them
   (`COMPOSE_FILE` defaults to `/srv/nethackers/compose.yaml`):

   ```bash
   sudo install -m 0644 deploy/compose.yaml /srv/nethackers/compose.yaml
   sudo install -m 0644 deploy/Caddyfile /srv/nethackers/Caddyfile
   ```

   Deploys never touch this copy, so a change to `deploy/compose.yaml` in
   the repository has to be installed again by hand.

4. The environment file:

   ```bash
   sudo install -m 0640 -o nethacker -g nethacker deploy/hub.env.example /etc/nethackers/hub.env
   sudo -e /etc/nethackers/hub.env   # the client id, a real digest-pinned image, the private-tier values
   ```

5. Start the stack by hand, once. This is the only time `docker compose`
   runs directly; it starts Caddy, which `deploy-hub.sh` never touches, and
   lets it obtain the certificate:

   ```bash
   docker compose --env-file /etc/nethackers/hub.env -f /srv/nethackers/compose.yaml up -d
   ```

Verify: `deploy-hub.sh status` and the `curl` above. From here on, every
change goes through `deploy-hub.sh`.

Access afterwards: humans reach `nethacker@nethackers-hub` from a machine
enrolled in the tailnet; CI joins as an ephemeral node with a scoped OAuth
client and leaves when the job ends. Public port 22 is closed by UFW, which
allows it only on `tailscale0`.

## Invariants

- Never add `ports:` to the hub service in `compose.yaml`. Docker's iptables
  rules run ahead of UFW's, so a published port is reachable from the
  internet whatever UFW says
  ([Docker's packet filtering page](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).
  The hub stays on `expose: ["8000"]`; only Caddy publishes ports.
- The CI key can only run `deploy-hub.sh`, because its `authorized_keys`
  line carries `restrict,command=` ([sshd(8)](https://man.openbsd.org/sshd#AUTHORIZED_KEYS_FILE_FORMAT)).
  Keep that prefix.
- Images are pinned by digest, never a moving tag; `deploy-hub.sh` refuses
  anything else.
- The hub container runs read-only with every capability dropped,
  `no-new-privileges`, and a pid limit (`pids_limit: 512`, `compose.yaml`);
  there is no CPU or memory cap on the hub by design, so it can size its
  workers to the machine. Its writable paths are `/data` and a tmpfs at
  `/tmp`. It runs as root inside that container, so `data/` stays
  root-owned; a dedicated non-root user is a follow-up.
- `nethacker` is in the `docker` group, which is root-equivalent on the host.
  The privilege boundary is the CI key's forced command, not the user.

## Host layout

```
/srv/nethackers/
├── compose.yaml          # installed by hand from deploy/compose.yaml; what deploy-hub.sh re-ups
├── .env -> /etc/nethackers/hub.env   # hand-made symlink so a bare `docker compose` works here
├── Caddyfile             # from deploy/Caddyfile; mounted read-only into caddy
├── data/                 # hub.sqlite3 (+ -wal, -shm); root-owned, written by the hub container
├── backups/              # root-owned, readable by group nethacker; the backup script's target, unmounted today
├── backup.sh             # the online-backup script; not in this repository, not scheduled
└── deploy-history.log    # append-only: timestamp, ref, previous ref, actor (always `dunnolab` today)
/etc/nethackers/
└── hub.env               # client id, image pin, private-tier values; deploy-hub.sh rewrites the image line
/usr/local/bin/
└── deploy-hub.sh         # installed by provision.sh; run by CI and by hand
```

Docker volumes `caddy_data` (certificates) and `caddy_config` are managed by
Compose.
