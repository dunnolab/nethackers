# Deploying the NetHackers hub

Operator (and agent) runbook for the global hub at `https://nethackers.dunnolab.ai`
(VM `45.91.237.200`, Tailscale hostname `nethackers-hub`). Two containers: the
FastAPI **hub**, internal only, behind **Caddy**, which terminates TLS and
provisions a Let's Encrypt certificate on its own.

Prod always runs a named release pinned to an image digest. Every change to
what is running, deploy or rollback, goes through one script,
`deploy/deploy-hub.sh`. CI runs it on a tag push; a human or an agent runs it
by hand over the tailnet. Nothing is ever built on the VM.

## Public code, private infrastructure

The rule for this repo: we develop fully in public, and the infrastructure we
run stays private. That is a security boundary (withhold operational access),
not a business one (withhold code). The litmus test comes verbatim from the
Twelve-Factor App:

> *"[The test is] whether the codebase could be made open source at any
> moment, without compromising any credentials."*
> [12factor.net/config](https://12factor.net/config)

That test passes today.

- No secrets live in this repo. The only value the hub needs is
  `NETHACKERS_CLIENT_ID`, the GitHub App's public client id. There is no
  client secret and no App private key anywhere in this deployment; the hub
  reads GitHub with the caller's own token.
- `*.env.example` is tracked; `.env` and `*.env` are gitignored. Every runtime
  value is either public (the client id) or injected on the VM, never in the
  tree.
- The deploy SSH key and the Tailscale OAuth credentials live in GitHub
  environment secrets. The GitHub Container Registry (GHCR) pull token is
  minted per CI job and expires when the job ends (see Network, below). None
  of it is committed.
- The container image is a private GHCR package. Not to hide secrets (anyone
  who can pull an image can inspect its layers) but as a control point on what
  runs in prod. Pulling it never leaves a standing credential on the box.

## How a deploy happens

One action is the entire production lever:

```bash
git tag vX.Y.Z && git push --tags
```

That fans out in CI (`.github/workflows/hub-image.yml`):

1. **`build-push`** builds the hub image from `hub/Dockerfile` and pushes it
   to the private `ghcr.io/dunnolab/nethackers-hub` package, tagged
   `@sha256:<digest>` and `vX.Y.Z`.
2. **`deploy`** (needs `build-push`, environment `production`) joins the
   tailnet as an ephemeral node, SSHes to the VM as `nethacker`, and runs
   `deploy-hub.sh deploy ghcr.io/dunnolab/nethackers-hub@sha256:<digest> --yes`.
   The script pulls the digest, boot-checks it on a throwaway port, flips with
   `docker compose up -d --no-deps hub`, health-checks the new container,
   `curl`s the live URL, and rolls back to the previous digest if the
   post-flip health or URL check fails.

Every prod deploy is a versioned release, pinned to a digest, recorded in
`/srv/nethackers/deploy-history.log`. No bare commits, no local tags, no
on-VM builds.

Every `v*` tag deploys the hub by default. The hub reports its own package
version in the masthead, and the public website is
`src/nethackers/hub/web/index.html`, served by the hub, so even a
client-looking release can be hub-relevant, and a missed one would leave prod
quietly stale. The flip itself is a cheap, auto-verified blip. To skip a
release you know doesn't need to touch prod (a pure client patch, or
mid-incident), put `[skip hub-deploy]` in the release commit message.

> **`[skip hub-deploy]` is best-effort, not a guarantee.** The deploy job's
> `if:` checks `github.event.head_commit.message` for the marker, and GitHub
> leaves that field null on some tag-push payloads. When it is null the check
> can't see the marker and the job deploys anyway. This fails open
> (deploy-by-default), which is the safe direction here: a needless deploy is
> a few-second blip that is auto-verified and auto-rolled-back if anything is
> actually wrong. Don't rely on `[skip hub-deploy]` to keep a release off
> prod. If that ever matters, confirm with `deploy-hub.sh status` after
> pushing the tag.
>
> The check matches the whole message, body included. Don't mention the
> marker in the body of a commit you do want deployed.

## Agent runbook

Copy-paste these. They are the whole interface.

**To deploy:** tag a release and push it.

```bash
git tag vX.Y.Z && git push --tags
```

Watch the `Hub image` workflow in Actions. The `deploy` job does everything
else and reports pass/fail on the run itself.

**To check what's running**, SSH over the tailnet and ask the box:

```bash
ssh nethacker@nethackers-hub deploy-hub.sh status
```

Prints the running image ref, container health, live-URL status, and the
last 5 deploy-history entries.

**To deploy or roll back by hand** (emergencies only; a normal deploy is a
tag, above), SSH over the tailnet and run the same subcommands CI runs:

```bash
# deploy a specific digest (needs a GHCR token with read:packages on stdin)
printf '%s' "<ghcr token>" | \
  ssh nethacker@nethackers-hub deploy-hub.sh deploy \
  ghcr.io/dunnolab/nethackers-hub@sha256:<digest> --yes

# roll back to whatever was running before the last deploy. No token needed:
# it re-pins an image already on the box, so it works even if GHCR is down.
ssh nethacker@nethackers-hub deploy-hub.sh rollback
```

Add `--dry-run` to `deploy` to print the plan and change nothing. It still
reads (and discards) stdin first, so pipe something in or it will sit waiting
for input.

**Never build on the VM.** No `docker build`, no `git archive | ssh`, no
hand-editing `/etc/nethackers/hub.env` and re-upping. `deploy-hub.sh deploy
<digest>` and `deploy-hub.sh rollback` are the only sanctioned ways to change
what's running: the same script CI runs, whether a human types it or an agent
does.

## Network: Tailscale

The VM is enrolled in a Tailscale tailnet as `nethackers-hub`. Public port 22
is closed: UFW allows `:22` only from the `tailscale0` interface and denies it
from the internet entirely. `:80` and `:443` stay public (that's the website).

- Humans reach `nethacker@nethackers-hub` by enrolling their own machine in
  the tailnet and using Tailscale's MagicDNS name (`nethackers-hub`) or its
  stable `100.x` address.
- CI joins as an ephemeral node: the `deploy` job authenticates with a scoped
  Tailscale OAuth client (`tag:ci`), joins the tailnet for the job, SSHes over
  the private link, and leaves when the job ends. No standing CI machine, and
  `:22` never opens to the internet.
- An internet port scan of `45.91.237.200` finds no SSH port at all. Nothing
  to brute-force, and a future OpenSSH CVE isn't reachable from outside the
  tailnet.
- The CI deploy key is forced-command: `authorized_keys` restricts it to
  running `/usr/local/bin/deploy-hub.sh`, so even a leaked key can't do
  anything beyond what that script allows.

## The Docker/UFW invariant

**Never add `ports:` to the hub service in `compose.yaml`.** Docker inserts
its own iptables rules ahead of UFW's, so a published container port
(`ports: ["8000:8000"]`) is reachable from the internet even with `ufw deny`
in place. The firewall never gets a vote
([Docker packet filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).

The hub stays on `expose: ["8000"]`, reachable from `caddy` over the compose
network and never published to the host. Only `caddy` publishes ports (`80`,
`443`, `443/udp`), because it is the one thing meant to be public. If the hub
ever needs to be reached directly, route it through Caddy, never through
`ports:` on the hub service.

## Rollback runbook

```bash
ssh nethacker@nethackers-hub deploy-hub.sh rollback
```

`rollback` re-pins `NETHACKERS_HUB_IMAGE` to the digest recorded as
"previous" in the last line of `/srv/nethackers/deploy-history.log`, one step
back from whatever is running now, and re-ups with `--no-deps` (Caddy
untouched). It doesn't pull: the previous image is already on the box from
when it was deployed, so rollback works even if GHCR is unreachable.

The deploy script also does this automatically: any failure in the post-flip
health check or live-URL check inside `deploy-hub.sh deploy` triggers the same
restore-and-re-up, so most bad deploys never need a manual rollback. Reach for
the command above when a deploy passed its own health check but is bad in a
way the script can't detect, such as a logic regression that still returns
200 on `/healthz`.

`deploy-hub.sh status` prints the last 5 history lines; the full log is plain
tab-separated text (`timestamp`, `ref`, `previous ref`, `actor`) at
`/srv/nethackers/deploy-history.log`.

## Host layout

Everything the deploy touches, after provisioning:

```
/srv/nethackers/
├── data/                 # hub SQLite DB (hub.sqlite3); root-owned, written by the hub container
├── backups/              # your DB snapshots (root:nethacker, mode 0750)
├── Caddyfile             # copied from deploy/Caddyfile; mounted read-only into caddy
└── deploy-history.log    # append-only deploy log (root:nethacker, mode 0664)
/etc/nethackers/
└── hub.env               # NETHACKERS_CLIENT_ID + NETHACKERS_HUB_IMAGE, mode 0640, owned
                           # nethacker:nethacker — deploy-hub.sh rewrites the image line in
                           # place on every deploy, so the deploy user must own the file
/usr/local/bin/
└── deploy-hub.sh          # the deploy script; installed by provision.sh, run by CI and by hand
~nethacker/nethackers/deploy/   # a clone of this repo; compose.yaml lives here (COMPOSE_FILE default)
```

Docker volumes `caddy_data` (certs) and `caddy_config` are managed by
Compose.

## First-time provisioning

Run once per VM. Read this whole section before starting; the lockout
warning below is not optional.

1. **DNS.** Create an `A` record `nethackers.dunnolab.ai → 45.91.237.200`
   and let it propagate before first start. Caddy needs the name to resolve
   to this host to complete the ACME HTTP/TLS challenge.

2. **Provision the VM**, from a checkout (`deploy-hub.sh` must sit next to
   `provision.sh`; the script installs it to `/usr/local/bin/`):

   ```bash
   sudo TS_AUTHKEY=<one-time tailnet auth key> ./provision.sh \
     "<human ssh-ed25519 key>" "<CI deploy ssh-ed25519 key>"
   ```

   It installs Docker Engine + Compose v2, UFW, unattended-upgrades, and
   Tailscale; enrolls the box in the tailnet as `nethackers-hub`; creates
   the `nethacker` deploy user with two keys in `authorized_keys`, the
   human key (a normal shell) and the CI key (forced-command, restricted to
   `deploy-hub.sh`); adds 2 GiB swap if none exists; disables SSH password
   auth; and closes public `:22`, leaving SSH reachable only over the
   tailnet. It's idempotent, so it is safe to re-run.

   > **Lockout warning.** This script closes public port 22. Run it
   > without `TS_AUTHKEY`, or before you've confirmed the box is actually
   > reachable over the tailnet, and you lose SSH access completely. There
   > is no fallback path back in.
   >
   > - `TS_AUTHKEY` is required, not optional, for a box you intend to keep
   >   using.
   > - Run `provision.sh` from your cloud provider's console (serial or
   >   VNC), not an SSH session over the public internet. If anything goes
   >   wrong mid-script, a console session survives; an internet SSH session
   >   dies along with the port it's using.
   > - Before doing anything else afterward, verify `nethacker` SSH works
   >   over the tailnet (`ssh nethacker@nethackers-hub`, from a machine
   >   already enrolled). Only then drop console/root access.

3. **Get the deploy files onto the host** as `nethacker`: clone this repo
   into `~/nethackers` (matches `deploy-hub.sh`'s default `COMPOSE_FILE`),
   then place the Caddyfile where Compose expects it:

   ```bash
   sudo install -m 0644 deploy/Caddyfile /srv/nethackers/Caddyfile
   ```

4. **Configure the environment file** (public client id + pinned image),
   mode `0640`, owned `nethacker:nethacker`:

   ```bash
   sudo install -m 0640 -o nethacker -g nethacker deploy/hub.env.example /etc/nethackers/hub.env
   sudo -e /etc/nethackers/hub.env   # set NETHACKERS_CLIENT_ID + a real digest-pinned NETHACKERS_HUB_IMAGE
   ```

5. **Bring up the stack once, by hand.** This is the one and only time you
   run `docker compose` directly. It starts Caddy for the first time (which
   `deploy-hub.sh` never touches) and lets it obtain its certificate:

   ```bash
   cd ~/nethackers/deploy
   docker compose --env-file /etc/nethackers/hub.env -f compose.yaml up -d
   ```

   From here on, every change to the running hub goes through
   `deploy-hub.sh`: tag a release, or run it by hand over the tailnet.
   Don't run `docker compose up` again.

## Verify

```bash
ssh nethacker@nethackers-hub deploy-hub.sh status    # running ref, health, live URL
curl --fail https://nethackers.dunnolab.ai/healthz    # -> {"status":"ok"}
```

The hub is never exposed directly; reach it only through Caddy on 443.
Caddy also serves plain `:80` and redirects it to HTTPS.

## Notes

- The hub container runs read-only with all Linux capabilities dropped, no
  new privileges, and tight CPU/memory/pids limits (see `compose.yaml`). Its
  only writable paths are the `/data` volume and a small `tmpfs` at `/tmp`.
- The hub image runs as root inside that locked-down container (all caps
  dropped, no-new-privileges, read-only rootfs), so `data/` and `backups/`
  stay root-owned and the SQLite DB is writable with no chown dance. Running
  the hub as a dedicated nonroot user is a hardening follow-up (see the
  design spec's public-launch gaps); if you switch, chown those directories
  to that uid.
- The `nethacker` deploy user is in the `docker` group, which is effectively
  root-equivalent (a container can mount the host filesystem). The real
  privilege boundary is the forced-command restriction on the CI key, not
  the user's non-root-ness. Don't relax that restriction.
- Self-hosting is unsupported: you host it, you own it.
