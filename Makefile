# NetHackers -- dev convenience targets. Requires: uv, docker.
.PHONY: install uninstall hub hub-down hub-reset test check smoke

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

# Fast suite (no NLE/Docker), types+lint, and the isolated docker-compose smoke.
test:
	uv run pytest -m "not nle and not docker" -q
check:
	uv run mypy src/nethackers tests
	uv run ruff check .
smoke:
	uv run pytest -m docker -q
