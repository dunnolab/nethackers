# NetHackers -- dev convenience targets. Requires: uv, docker, curl.
.PHONY: install uninstall arena up down wait-hub hub hub-down hub-reset test check smoke

# Arena eval image tag (override: `make up ARENA_IMAGE=you/arena:tag`).
ARENA_IMAGE ?= nethackers/arena:dev

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

# Build the pinned arena eval image (compiles NLE + AutoAscend -- slow the
# first time, Docker-layer-cached after; rebuilds when src/ changes, so the
# image is never stale). `nethackers evolve`/`eval` default to $(ARENA_IMAGE).
arena:
	docker build -f arena/Dockerfile -t $(ARENA_IMAGE) .

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
	uv run pytest -m "not nle and not docker and not claude_live" -q
check:
	uv run mypy src/nethackers tests
	uv run ruff check .
smoke:
	uv run pytest -m docker -q
