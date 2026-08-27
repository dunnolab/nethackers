#!/usr/bin/env bash
# Deploy / roll back the NetHackers hub. Runs on the VM; invoked by CI over
# forced-command SSH or by a human/agent. See deploy/README.md.
# Bash 3.2+; never eval untrusted input.
set -euo pipefail
export LC_ALL=C  # byte-wise (case-sensitive) glob/sort/collation regardless of caller's locale

IMAGE_REPO="ghcr.io/dunnolab/nethackers-hub"
HUB_ENV_FILE="${HUB_ENV_FILE:-/etc/nethackers/hub.env}"
DEPLOY_HISTORY="${DEPLOY_HISTORY:-/srv/nethackers/deploy-history.log}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/nethacker/nethackers/deploy/compose.yaml}"
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:-https://nethackers.dunnolab.ai/healthz}"
GHCR_USER="${GHCR_USER:-}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
DRY_RUN=0
ASSUME_YES=0

log()  { printf '  %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
deploy-hub.sh — deploy / roll back the NetHackers hub

  deploy <image-ref>   pull the digest, boot-check, flip, verify, auto-rollback on failure
  rollback             re-pin the previous digest from the deploy history and re-up
  status               show running image, health, live URL, recent deploys

Options: --yes (non-interactive)  --dry-run (print plan, change nothing)  --help
The deploy subcommand reads a GHCR token on stdin (empty ⇒ skip docker login).
Image ref must be ghcr.io/dunnolab/nethackers-hub@sha256:<64 hex>.
EOF
}

validate_ref() {
  case "$1" in
    ghcr.io/dunnolab/nethackers-hub@sha256:*)
      local d="${1#*@sha256:}"
      [ "${#d}" -eq 64 ] || die "digest must be 64 hex chars: $1"
      case "$d" in *[!0-9a-f]*) die "digest not lowercase hex: $1" ;; esac
      ;;
    *) die "image ref must be a $IMAGE_REPO@sha256:<digest> (got: $1)" ;;
  esac
}

# Placeholders filled in later tasks:
cmd_deploy()   { die "deploy not implemented yet"; }
cmd_rollback() { die "rollback not implemented yet"; }
cmd_status()   { echo "status: not implemented yet"; }

main() {
  # Under a forced-command key, sshd passes the request in SSH_ORIGINAL_COMMAND
  # with no positional args. Parse it as `subcommand [arg]` — never eval it.
  if [ "$#" -eq 0 ] && [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    # shellcheck disable=SC2086  # deliberate: split into whitelisted words; noglob prevents expansion
    set -f; set -- $SSH_ORIGINAL_COMMAND; set +f
  fi
  local sub="${1:-}"; shift || true
  local ref=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --yes) ASSUME_YES=1 ;;
      --help|-h) usage; exit 0 ;;
      -*) die "unknown option: $1" ;;
      *) ref="$1" ;;
    esac
    shift
  done
  case "$sub" in
    --help|-h|"" ) usage; [ -z "$sub" ] && exit 1 || exit 0 ;;
    deploy)   [ -n "$ref" ] || die "deploy needs an image ref"; validate_ref "$ref"; cmd_deploy "$ref" ;;
    rollback) cmd_rollback ;;
    status)   cmd_status ;;
    *) die "unknown subcommand: $sub" ;;
  esac
}
main "$@"
