# Hub API server image (M2a Task 14). Builds this repo's `nethackers`
# package with the `hub` extra (fastapi/uvicorn/httpx -- see pyproject.toml)
# and runs the uvicorn --factory entrypoint (nethackers.hub.api:
# create_default_app, task-14-context.md's Reconciliation 2: a factory, so
# the store/DB is only ever opened at server startup, never at import/build
# time). Build context is the repo root (see compose.yaml's
# `build.context: .` + `build.dockerfile: hub/Dockerfile`); the image only
# needs pyproject.toml + src/ (mirrors arena/Dockerfile's minimal COPY set
# -- .dockerignore already excludes tests/docs/roots/etc for both images).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[hub]"

EXPOSE 8000
CMD ["uvicorn", "nethackers.hub.api:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
