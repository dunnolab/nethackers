# NetHackers -- dev convenience targets. Requires: uv, docker, curl.
.PHONY: install uninstall nle-base arena mutator up down wait-hub hub hub-down hub-reset test check smoke

# Shared base image tag: compiled NLE + deps, NO nethackers source.
NLE_BASE_IMAGE ?= nethackers/nle-base:dev
# Arena eval image tag (override: `make up ARENA_IMAGE=you/arena:tag`).
ARENA_IMAGE ?= nethackers/arena:dev
# Mutator sandbox image tag (override: `make mutator MUTATOR_IMAGE=you/mutator:tag`).
MUTATOR_IMAGE ?= nethackers/mutator:latest

# Install the `nethackers` CLI into an isolated uv tool env. The cache-clean is
# required because the package version is pinned 0.0.0, so uv would otherwise
# reuse a stale path-build wheel and ignore local source changes (`uv run
# nethackers ...` from the repo always uses live source and needs none of this).
install:
	uv cache clean nethackers
	uv tool install --from . nethackers --force

uninstall:
	uv tool uninstall nethackers

# Local hub stack: http://localhost:8000, seeded with the fixture dataset.
hub:
	docker compose up -d --build
hub-down:
	docker compose down
hub-reset:
	docker compose down -v && docker compose up -d --build

# Build the shared base (compiles NLE from source -- slow the first time,
# Docker-layer-cached after; rebuilds only when pyproject/uv.lock change). Both
# the arena scorer and the mutator sandbox build FROM this, so NLE compiles once
# -- and the mutator inherits NLE WITHOUT the nethackers package (the info-diet
# wall is the image boundary; see nle-base/Dockerfile).
nle-base:
	docker build -f nle-base/Dockerfile -t $(NLE_BASE_IMAGE) .

# Build the pinned arena eval image FROM nle-base (adds our source, for
# scoring). Rebuilds when src/ changes; the NLE compile stays cached in
# nle-base. `nethackers evolve`/`eval` default to $(ARENA_IMAGE).
arena: nle-base
	docker build -f arena/Dockerfile --build-arg NLE_BASE=$(NLE_BASE_IMAGE) -t $(ARENA_IMAGE) .

# Build the mutator sandbox image FROM nle-base (NOT arena): same compiled NLE,
# but no nethackers CLI and no harness/ (no seed formula) -- only the seed-free
# arena+contracts scoring kit for parity, plus the harness CLIs + a non-root
# `agent` user. See Dockerfile.mutator. `nethackers evolve` defaults to
# $(MUTATOR_IMAGE).
mutator: nle-base
	docker build -f Dockerfile.mutator --build-arg NLE_BASE=$(NLE_BASE_IMAGE) -t $(MUTATOR_IMAGE) .

# ONE command to bring the whole local stack up: (re)build the arena image,
# (re)build + start the hub, then wait until the hub answers. Run this before
# `nethackers evolve`.
up: arena hub wait-hub
	@echo "✓ stack up  ·  hub http://localhost:8000  ·  arena image $(ARENA_IMAGE)"

# Tear the whole stack down (stops the hub container; arena image is kept).
down: hub-down

# Poll the hub until it serves the objective catalog (uvicorn needs a beat).
wait-hub:
	@printf 'waiting for hub'; \
	for _ in $$(seq 1 60); do \
		if curl -fsS http://localhost:8000/objectives >/dev/null 2>&1; then echo ' · ready'; exit 0; fi; \
		printf '.'; sleep 1; \
	done; \
	echo; echo 'hub not ready on :8000' >&2; exit 1

# Fast suite (no NLE/Docker/live-Claude), types+lint, and the isolated
# docker-compose smoke.
test:
	uv run pytest -m "not nle and not docker and not claude_live and not codex_live" -q
check:
	uv run mypy src/nethackers tests
	uv run ruff check .
smoke:
	uv run pytest -m docker -q
