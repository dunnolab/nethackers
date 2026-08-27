#!/usr/bin/env bash
# Provision the NetHackers hub VM. Run ONCE, as root, passing the deploy public key:
#   sudo ./provision.sh "ssh-ed25519 AAAA... deploy@nethackers"
# Idempotent (safe to re-run). Installs Docker Engine + Compose v2, UFW, and
# unattended-upgrades; creates the `nethacker` deploy user; adds 2 GiB swap if the
# host has none; disables SSH password auth; lays out /srv/nethackers/{data,backups}.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    printf 'Run this script as root.\n' >&2
    exit 1
fi

deploy_key=${1:-}
if [[ ! ${deploy_key} =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ .+ ]]; then
    printf 'Usage: %s "ssh-ed25519 AAAA... label"\n' "$0" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes ca-certificates curl docker.io docker-compose-v2 ufw unattended-upgrades
systemctl enable --now docker
systemctl enable --now unattended-upgrades

# Deploy user (idempotent): docker group + key-only SSH.
id nethacker >/dev/null 2>&1 || useradd --create-home --shell /bin/bash nethacker
usermod --append --groups docker nethacker
install -d -m 0700 -o nethacker -g nethacker /home/nethacker/.ssh
printf '%s\n' "${deploy_key}" >/home/nethacker/.ssh/authorized_keys
chown nethacker:nethacker /home/nethacker/.ssh/authorized_keys
chmod 0600 /home/nethacker/.ssh/authorized_keys

# Host layout. The hub container runs as root (see hub/Dockerfile) with all Linux
# capabilities dropped, no-new-privileges, and a read-only rootfs; it writes its
# SQLite DB to the /data bind mount. data/+backups/ stay root-owned (group nethacker
# can read backups). Running the hub image as a dedicated nonroot user is a
# documented hardening follow-up; if you switch to one, chown these to that uid.
install -d -m 0750 -o nethacker -g nethacker /srv/nethackers
install -d -m 0750 -o root -g nethacker /srv/nethackers/data /srv/nethackers/backups
install -d -m 0750 -o root -g nethacker /etc/nethackers

# CI deploy public key (forced-command). Passed as $2.
ci_deploy_key=${2:-}
if [[ -n ${ci_deploy_key} && ! ${ci_deploy_key} =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+ ]]; then
    printf 'Second arg, if given, must be an ssh-ed25519 public key.\n' >&2
    exit 1
fi

# Install the deploy script (repo copy sits next to this script during provisioning).
install -m 0755 "$(dirname "$0")/deploy-hub.sh" /usr/local/bin/deploy-hub.sh
install -d -m 0750 -o root -g nethacker /srv/nethackers
touch /srv/nethackers/deploy-history.log
chown root:nethacker /srv/nethackers/deploy-history.log
chmod 0664 /srv/nethackers/deploy-history.log

if [[ -n ${ci_deploy_key} ]]; then
    authk=/home/nethacker/.ssh/authorized_keys
    line="restrict,command=\"/usr/local/bin/deploy-hub.sh\" ${ci_deploy_key}"
    touch "${authk}"; chown nethacker:nethacker "${authk}"; chmod 0600 "${authk}"
    key_body=${ci_deploy_key#* }      # strip the "ssh-ed25519 " type prefix
    key_body=${key_body%% *}          # strip any trailing " comment"
    grep -qF "${key_body}" "${authk}" || printf '%s\n' "${line}" >> "${authk}"
fi

# 2 GiB swap only if the host has none.
if ! swapon --show=NAME --noheadings | grep --quiet .; then
    [[ -e /swapfile ]] || fallocate -l 2G /swapfile
    chmod 0600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep --quiet --extended-regexp '^/swapfile[[:space:]]' /etc/fstab \
        || printf '/swapfile none swap sw 0 0\n' >>/etc/fstab
fi

# SSH hardening: key-only, no passwords.
hardening=/etc/ssh/sshd_config.d/99-nethackers-hardening.conf
printf '%s\n' \
    'PasswordAuthentication no' \
    'KbdInteractiveAuthentication no' \
    'PermitRootLogin prohibit-password' \
    'PubkeyAuthentication yes' >"${hardening}"
chmod 0644 "${hardening}"
sshd -t
systemctl reload ssh

# Firewall: allow only SSH + HTTP(S) (incl. HTTP/3 over UDP 443).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp comment 'NetHackers HTTP -> HTTPS redirect'
ufw allow 443/tcp comment 'NetHackers HTTPS'
ufw allow 443/udp comment 'NetHackers HTTP/3'
ufw --force enable

docker compose version
printf 'Provisioning complete. Verify SSH as nethacker before dropping root access.\n'
