# Pinned, deterministic runtime for evaluating NetHackers submissions.
#
# Builds an image containing this repo's `nethackers` package (with the NLE
# and AutoAscend-solution extras installed) whose ENTRYPOINT is the
# `nethackers.arena.run` CLI (see src/nethackers/arena/run.py). A submission
# is mounted at runtime (e.g. `-v $PWD/roots/autoascend:/sol:ro`) and passed
# via `--solution /sol`; this image intentionally does not bake in any
# submission so the same image can evaluate any solution.
#
# The ENV block pins the reproducibility contract: PYTHONHASHSEED=0 keeps
# Python's hash randomization from perturbing dict/set iteration order across
# runs, and the *_NUM_THREADS=1 variables force single-threaded BLAS/OpenMP
# math so floating-point reduction order (and therefore trajectory outcomes)
# is stable run-to-run and host-to-host -- this now also protects
# determinism under M3's in-container parallel eval fan-out (multiple
# worker processes sharing the host's cores), where an oversubscribed BLAS
# thread pool could otherwise perturb per-worker floating-point results.
FROM python:3.11-slim

ENV PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git libbz2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/nethackers
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[nle,autoascend]"

ENTRYPOINT ["python", "-m", "nethackers.arena.run"]
