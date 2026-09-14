#!/bin/sh
# Entrypoint shim for nethackers/mutator: starts as root only long enough to
# align the non-root `agent` account's UID/GID with whichever host user owns
# the bind-mounted /workspace, then drops to `agent` (via gosu) and execs
# whatever command the caller supplied -- ContainerOperator's per-iteration
# `timeout <n> claude|codex ...` (harness/container_operator.py's
# build_docker_argv), or an ad hoc `docker run --entrypoint ...
# nethackers/mutator:latest ...` smoke check. See Dockerfile.mutator's header
# comment for why this exists (Claude Code refuses
# --dangerously-skip-permissions as root).
#
# PUID/PGID resolution, in priority order:
#   1. Explicit `-e PUID=`/`-e PGID=` -- the caller knows best.
#   2. /workspace's own owning uid/gid, auto-detected (it is already
#      mounted by the time this script runs) -- works out of the box with
#      today's `docker run -v $WORKTREE:/workspace`, no explicit -e PUID/
#      -e PGID required. The common case.
#   3. The image's baked-in default (1000:1000), if /workspace isn't
#      mounted at all (e.g. a bare smoke-test `docker run`, no -v).
set -eu

target_uid="${PUID:-}"
target_gid="${PGID:-}"
if [ -z "$target_uid" ] && [ -d /workspace ]; then
    target_uid="$(stat -c '%u' /workspace)"
fi
if [ -z "$target_gid" ] && [ -d /workspace ]; then
    target_gid="$(stat -c '%g' /workspace)"
fi
target_uid="${target_uid:-1000}"
target_gid="${target_gid:-1000}"

# Never remap onto uid/gid 0 -- that would hand `agent` root, defeating the
# non-root cage. Leave the baked-in 1000:1000 in that (unlikely) case.
if [ "$target_uid" != "0" ] && [ "$target_gid" != "0" ]; then
    current_uid="$(id -u agent)"
    current_gid="$(id -g agent)"
    if [ "$target_gid" != "$current_gid" ]; then
        groupmod -o -g "$target_gid" agent
    fi
    if [ "$target_uid" != "$current_uid" ]; then
        usermod -o -u "$target_uid" agent
    fi
fi

# Only ever chown directories that are always this image's own filesystem
# layer, never their contents or a bind-mounted file. OpenCode writes under
# XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_STATE_HOME, and XDG_CACHE_HOME; their
# baked ownership still has uid 1000 after `usermod` changes agent's uid, so
# every directory component must be realigned here. Never chown -R: .codex is
# bind-mounted read-write from the host's real ~/.codex (auth_inject.py),
# and a recursive chown would silently rewrite ownership of the HOST's
# actual files through that mount.
for owned_dir in \
    /home/agent \
    /home/agent/.claude \
    /home/agent/.config \
    /home/agent/.config/opencode \
    /home/agent/.local \
    /home/agent/.local/share \
    /home/agent/.local/share/opencode \
    /home/agent/.local/state \
    /home/agent/.local/state/opencode \
    /home/agent/.cache \
    /home/agent/.cache/opencode
do
    if [ -d "$owned_dir" ]; then
        chown agent:agent "$owned_dir"
    fi
done

exec gosu agent "$@"
