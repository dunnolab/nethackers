# NetHackers -- dev convenience targets. Requires: uv, docker, curl.
.PHONY: install uninstall nle-base arena mutator stack up down wait-hub hub hub-down hub-reset test check smoke

# Load this worktree's allocated stage vars as MAKE variables (not just shell
# env), so `?=`/`$(or ...)` defaults below (ARENA_IMAGE, MUTATOR_IMAGE) can
# see e.g. NETHACKERS_ARENA_IMAGE at parse time; no-op if the file doesn't
# exist yet (leading `-`). Each `KEY=value` line in .env.stack is also valid
# Makefile variable-assignment syntax, so `include` parses it directly.
# Distinct from SOURCE_STACK below, which sources the same file into a
# recipe's *shell* for `$$VARNAME` use inside commands (e.g. compose).
-include .env.stack

# Source this worktree's allocated stage vars (COMPOSE_PROJECT_NAME,
# NETHACKERS_HUB_PORT, ...) into the current recipe shell, if .env.stack
# exists; no-op otherwise (compose then falls back to its own defaults --
# dirname project, :8000). Guarded with `[ -f ]` rather than the more obvious
# `. ./.env.stack 2>/dev/null || true`: on a missing file, `.` is a POSIX
# "special builtin" whose failure aborts a non-interactive shell outright --
# `/bin/sh` on macOS and `dash` on Debian/Ubuntu both do this -- so `|| true`
# never even runs. Must stay on the SAME shell line (via `\`) as the command
# that needs the vars; each recipe line is its own subshell.
SOURCE_STACK = set -a; [ -f ./.env.stack ] && . ./.env.stack; set +a

# Shared base image tag: compiled NLE + deps, NO nethackers source.
NLE_BASE_IMAGE ?= nethackers/nle-base:dev
# Arena eval image tag: this worktree's own throwaway tag from .env.stack
# (nethackers/arena:<slug>) when allocated, else the shared dev fallback;
# explicit override always wins: `make up ARENA_IMAGE=you/arena:tag`.
ARENA_IMAGE   ?= $(or $(NETHACKERS_ARENA_IMAGE),nethackers/arena:dev)
# Mutator image tag for a manual `make mutator` only -- experiments on the base,
# used together with NETHACKERS_MUTATOR_IMAGE. nethackers itself runs a checkout on
# nethackers/mutator:h-<fingerprint>, which it pulls or builds on its own.
MUTATOR_IMAGE ?= $(or $(NETHACKERS_MUTATOR_IMAGE),nethackers/mutator:latest)
# The platform every sandbox image is built for, emulated on other hosts (Apple
# Silicon: enable Rosetta, see `nethackers doctor`). Same value as
# image_inputs.REFERENCE_PLATFORM: NetHack plays a different game per seed on
# another architecture, and evolve refuses an arena and mutator built for
# different platforms, because the coding agent would then optimize games the
# arena never scores.
IMAGE_PLATFORM ?= linux/amd64
# Local hub auth provider (see "Real-auth local hub mode",
# docs/local-stack.md): `stub` (default) runs bare `docker compose up`,
# letting the checked-in compose.override.yaml auto-merge -- offline
# LocalStubAuth + fixtures, today's behavior; `github` runs an explicit
# `-f compose.yaml -f compose.github.yaml`, which *disables* that
# auto-merge, so NETHACKERS_STUB_IDENTITIES is unset and
# create_default_app (hub/api.py) selects the real GitHubAppAuth instead.
# `make up HUB_AUTH=github` is the whole flip.
HUB_AUTH ?= stub

# Install the `nethackers` CLI into an isolated uv tool env. The cache-clean is
# required because the package version is pinned 0.0.0, so uv would otherwise
# reuse a stale path-build wheel and ignore local source changes (`uv run
# nethackers ...` from the repo always uses live source and needs none of this).
install:
	uv cache clean nethackers
	uv tool install --from . nethackers --force

uninstall:
	uv tool uninstall nethackers

# Local hub stack: http://localhost:8000 (or this worktree's allocated port --
# see `make stack`). HUB_AUTH=stub (default) seeds the fixture dataset behind
# offline dev auth; HUB_AUTH=github runs real GitHubAppAuth against an empty
# DB (`make hub HUB_AUTH=github` / `make up HUB_AUTH=github`).
hub:
	$(SOURCE_STACK); \
	if [ "$(HUB_AUTH)" = "github" ]; then \
		docker compose -f compose.yaml -f compose.github.yaml up -d --build; \
	else \
		docker compose up -d --build; \
	fi
hub-down:
	$(SOURCE_STACK); \
	docker compose down
hub-reset:
	$(SOURCE_STACK); \
	docker compose down -v && docker compose up -d --build

# Build the shared base (compiles NLE from source -- slow the first time,
# Docker-layer-cached after; rebuilds only when pyproject/uv.lock change). Both
# the arena scorer and the mutator sandbox build FROM this, so NLE compiles once
# -- and the mutator inherits NLE WITHOUT the nethackers package (the info-diet
# wall is the image boundary; see nle-base/Dockerfile).
nle-base:
	docker build --platform $(IMAGE_PLATFORM) -f nle-base/Dockerfile -t $(NLE_BASE_IMAGE) .

# Build the pinned arena eval image FROM nle-base (adds our source, for
# scoring). Rebuilds when src/ changes; the NLE compile stays cached in
# nle-base. `nethackers evolve`/`eval` default to $(ARENA_IMAGE).
arena: nle-base
	docker build --platform $(IMAGE_PLATFORM) -f arena/Dockerfile --build-arg NLE_BASE=$(NLE_BASE_IMAGE) -t $(ARENA_IMAGE) .

# Build the mutator sandbox image FROM nle-base (NOT arena): same compiled NLE,
# but no nethackers CLI and no harness/ (no seed formula) -- only the seed-free
# arena+contracts scoring kit for parity, plus the harness CLIs + a non-root
# `agent` user. See Dockerfile.mutator. `nethackers evolve` defaults to
# $(MUTATOR_IMAGE).
mutator: nle-base
	docker build --platform $(IMAGE_PLATFORM) -f Dockerfile.mutator --build-arg NLE_BASE=$(NLE_BASE_IMAGE) -t $(MUTATOR_IMAGE) .

# Allocate/refresh this worktree's .env.stack (deterministic host port +
# compose project name, so parallel worktrees never collide); gitignored,
# reused verbatim after the first run. See scripts/stack.py.
stack: ; @uv run python scripts/stack.py >/dev/null

# Bring up this worktree's local stack. Order matters: allocate the stage,
# then start + wait for the HUB FIRST -- that alone is everything you need to
# browse boards / drive the TUI -- and only THEN build the arena eval image
# (needed solely for `nethackers evolve`). A slow or failing arena build can
# no longer gate the hub. To browse only, `make hub` on its own (no arena
# build) is enough.
up: stack hub wait-hub arena
	@$(SOURCE_STACK); \
	echo "✓ stack up  ·  hub $${NETHACKERS_HUB:-http://localhost:8000}  ·  arena image $(ARENA_IMAGE)"

# Tear the whole stack down (stops the hub container; arena image is kept).
down: hub-down

# Poll the hub until it serves the objective catalog (uvicorn needs a beat).
wait-hub:
	@$(SOURCE_STACK); \
	printf 'waiting for hub'; \
	for _ in $$(seq 1 60); do \
		if curl -fsS "http://localhost:$${NETHACKERS_HUB_PORT:-8000}/objectives" >/dev/null 2>&1; then echo ' · ready'; exit 0; fi; \
		printf '.'; sleep 1; \
	done; \
	echo; echo "hub not ready on :$${NETHACKERS_HUB_PORT:-8000}" >&2; exit 1

# Fast suite (no NLE/Docker/live-Claude), types+lint, and the isolated
# docker-compose smoke.
test:
	uv run pytest -m "not nle and not docker and not claude_live and not codex_live" -q
check:
	uv run mypy src/nethackers tests
	uv run ruff check .
smoke:
	uv run pytest -m docker -q
