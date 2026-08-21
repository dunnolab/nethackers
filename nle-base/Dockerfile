# Shared base: compiled NLE + pinned Python deps, and NO nethackers source.
#
# Both the scorer (arena/Dockerfile) and the mutator (Dockerfile.mutator) build
# FROM this, so NLE compiles exactly once -- and, crucially, the mutator inherits
# a byte-identical NLE WITHOUT the nethackers package. That is the info-diet wall
# (spec §3.6) made an image boundary rather than a matter of trust: the mutator
# physically cannot `import nethackers.harness.seeds` to derive the held-out
# seeds, because harness/ (and the whole package) is simply not installed here.
#
# Layering: third-party deps (NLE compiles from source) install in a layer keyed
# on pyproject.toml + uv.lock alone, so NLE is rebuilt only when the lockfile
# actually changes. The uv cache mount persists downloads across builds.
FROM python:3.11-slim

# NLE ships as source; build-essential/cmake compile it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git libbz2-dev && rm -rf /var/lib/apt/lists/*

# uv: fast, cache-friendly resolver/installer, pinned; drives the same uv.lock
# the repo develops against (no dep-list drift).
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /opt/nethackers

# Dependency layer ONLY -- `--no-install-project` installs NLE + deps into the
# venv but NOT the nethackers package itself. NLE's source build happens here,
# once, and is reused by both derived images.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra nle --extra autoascend

# .venv/bin on PATH -> `python` is the synced interpreter with NLE importable.
# PYTHONHASHSEED + single-threaded BLAS pin the reproducibility contract (stable
# dict order + floating-point reduction order) both derived images rely on.
ENV PATH="/opt/nethackers/.venv/bin:$PATH" \
    PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    PYTHONUNBUFFERED=1
