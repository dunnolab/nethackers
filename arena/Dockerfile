# Pinned, deterministic runtime for evaluating NetHackers submissions.
#
# Builds an image containing this repo's `nethackers` package (with the NLE and
# AutoAscend-solution extras) whose ENTRYPOINT is the `nethackers.arena.run` CLI
# (see src/nethackers/arena/run.py). A submission is mounted at runtime
# (`-v $PWD/roots/autoascend:/sol:ro`, `--solution /sol`); the image bakes in no
# submission, so one image evaluates any.
#
# FROM the shared nle-base (compiled NLE + deps): so NLE compiles once across
# the scorer AND the mutator. This image adds the app layer -- only our source,
# so a code edit invalidates just this fast step; the NLE compile stays cached.
ARG NLE_BASE=nethackers/nle-base:dev
FROM ${NLE_BASE}

# Application layer -- our source. `uv sync` (no longer --no-install-project)
# installs the nethackers package itself into the venv, for scoring.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-dev --extra nle --extra autoascend

ENTRYPOINT ["python", "-m", "nethackers.arena.run"]
