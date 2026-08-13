# Pinned, deterministic runtime for evaluating NetHackers submissions.
#
# Builds an image containing this repo's `nethackers` package (with the NLE
# and AutoAscend-solution extras) whose ENTRYPOINT is the
# `nethackers.arena.run` CLI (see src/nethackers/arena/run.py). A submission
# is mounted at runtime (`-v $PWD/roots/autoascend:/sol:ro`, `--solution
# /sol`); the image bakes in no submission, so one image evaluates any.
#
# Layering is deliberate. Third-party deps (NLE compiles from source here)
# install in a layer keyed ONLY on pyproject.toml + uv.lock, so editing our
# own src/ reuses the cached NLE build instead of recompiling it. NLE is
# rebuilt only when a dependency actually changes in the lockfile. The uv
# cache mount persists downloads across builds, so a flaky network never
# re-fetches an already-seen wheel.
FROM python:3.11-slim

# NLE ships as source; build-essential/cmake compile it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git libbz2-dev && rm -rf /var/lib/apt/lists/*

# uv: fast, cache-friendly resolver/installer, pinned; drives the same
# uv.lock the repo develops against (no dep-list drift).
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /opt/nethackers

# 1) Dependency layer -- cached on (pyproject.toml, uv.lock) alone. NLE's
#    source build happens here, exactly once, and is reused across every
#    later src/ edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra nle --extra autoascend

# 2) Application layer -- only our source. A code edit invalidates just this
#    fast step; the deps/NLE layer above stays cached.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-dev --extra nle --extra autoascend

# Runtime env lives BELOW the deps layer so changing a determinism pin never
# invalidates the NLE compile. .venv/bin on PATH -> `python` is the synced
# interpreter. PYTHONHASHSEED + single-threaded BLAS pin the reproducibility
# contract (stable dict order + floating-point reduction order), which also
# protects determinism under M3's in-container parallel-eval fan-out.
ENV PATH="/opt/nethackers/.venv/bin:$PATH" \
    PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "nethackers.arena.run"]
