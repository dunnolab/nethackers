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
GHCR_USER="${GHCR_USER:-dunnolab}"  # GHCR authenticates the pull by the token, not the username — it just needs to be non-empty (the package owner is a valid value).
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

compose() { docker compose --env-file "$HUB_ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

current_ref() { awk -F= '/^NETHACKERS_HUB_IMAGE=/{print $2}' "$HUB_ENV_FILE"; }

pin_ref() {  # rewrite NETHACKERS_HUB_IMAGE in place. In-place write (cat >), NOT mv: needs write on
             # the FILE only, so the non-root nethacker deploy user can re-pin a hub.env that lives
             # in a root-owned dir (it owns the file — see Contracts / Task 9).
  local ref="$1" tmp; tmp="$(mktemp)"
  awk -v r="$ref" '/^NETHACKERS_HUB_IMAGE=/{print "NETHACKERS_HUB_IMAGE=" r; next} {print}' \
    "$HUB_ENV_FILE" > "$tmp"
  grep -q '^NETHACKERS_HUB_IMAGE=' "$tmp" || printf 'NETHACKERS_HUB_IMAGE=%s\n' "$ref" >> "$tmp"
  cat "$tmp" > "$HUB_ENV_FILE"; rm -f "$tmp"
}

boot_check() {  # start the pulled image on a throwaway port + scratch DB, curl /healthz, tear down
  local ref="$1" name="hub-bootcheck-$$" port=18099
  docker run -d --rm --name "$name" -e NETHACKERS_DB=/tmp/scratch.sqlite3 \
    -e NETHACKERS_CLIENT_ID=bootcheck -p "127.0.0.1:${port}:8000" "$ref" >/dev/null \
    || return 1
  local ok=1 i=0
  while [ "$i" -lt 15 ]; do
    if curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then ok=0; break; fi
    i=$((i+1)); sleep "$HEALTH_INTERVAL"
  done
  docker rm -f "$name" >/dev/null 2>&1 || true
  return "$ok"
}

wait_healthy() {  # poll the running hub container's health until healthy (portable: no xargs -r)
  local i=0 cid status
  while [ "$i" -lt "$HEALTH_RETRIES" ]; do
    cid="$(compose ps -q hub 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || true)"
      [ "$status" = "healthy" ] && return 0
    fi
    i=$((i+1)); sleep "$HEALTH_INTERVAL"
  done
  return 1
}

record_history() {  # ts | tag-or-ref | previous-ref | actor
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "${GHCR_USER:-manual}" >> "$DEPLOY_HISTORY"
}

cmd_deploy() {
  local ref="$1" token prev
  token="$(cat)"                      # GHCR token on stdin (may be empty)
  prev="$(current_ref)"
  log "deploying $ref (was: ${prev:-none})"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] would: login/pull, boot-check, pin $ref, up --no-deps hub, health+URL check, rollback-on-fail"
    return 0
  fi
  if [ -n "$token" ]; then
    printf '%s' "$token" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
    trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
  fi
  docker pull "$ref" >/dev/null
  log "boot-check…"
  boot_check "$ref" || die "boot-check failed — image will not start; not flipping"
  record_history "$ref" "${prev:-none}"
  pin_ref "$ref"
  # --no-deps: never touch caddy (dodges depends_on hang). The flip is INSIDE the
  # guard so a failed `up` triggers rollback too, not a set -e abort.
  if ! { compose up -d --no-deps hub && wait_healthy && curl -fsS "$PUBLIC_HEALTH_URL" >/dev/null; }; then
    log "post-flip verification FAILED — rolling back to ${prev:-none}"
    if [ -n "${prev:-}" ]; then
      pin_ref "$prev"; compose up -d --no-deps hub || true
    fi
    die "deploy failed; rolled back to ${prev:-none}"
  fi
  log "deployed $ref"
}

cmd_rollback() {
  local prev
  prev="$(awk -F'\t' 'END{print $3}' "$DEPLOY_HISTORY" 2>/dev/null || true)"
  [ -n "$prev" ] && [ "$prev" != "none" ] || die "no previous digest in $DEPLOY_HISTORY"
  log "rolling back to $prev"
  [ "$DRY_RUN" -eq 1 ] && { log "[dry-run] would pin $prev and up --no-deps hub"; return 0; }
  local cur; cur="$(current_ref)"
  record_history "rollback:$prev" "$cur"
  pin_ref "$prev"; compose up -d --no-deps hub
  wait_healthy || die "hub unhealthy after rollback"
  log "rolled back to $prev"
}

cmd_status() {
  local cid; cid="$(compose ps -q hub 2>/dev/null || true)"
  printf 'running image: %s\n' "$(current_ref)"
  if [ -n "$cid" ]; then
    printf 'container health: %s\n' "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
  else
    printf 'container health: unknown\n'
  fi
  if curl -fsS "$PUBLIC_HEALTH_URL" >/dev/null 2>&1; then printf 'public URL: ok\n'; else printf 'public URL: DOWN\n'; fi
  printf 'recent deploys:\n'; tail -n 5 "$DEPLOY_HISTORY" 2>/dev/null || printf '  (none)\n'
}

main() {
  # Under a forced-command key, sshd passes the request in SSH_ORIGINAL_COMMAND
  # with no positional args. Parse it as `subcommand [arg]` — never eval it.
  if [ "$#" -eq 0 ] && [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    set -f
    # shellcheck disable=SC2086  # deliberate: split into whitelisted words; noglob prevents expansion
    set -- $SSH_ORIGINAL_COMMAND
    set +f
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
