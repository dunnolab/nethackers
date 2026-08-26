"""The evolve harness's own semantic version -- SEPARATE from the package
version. Bumped only when harness semantics change incompatibly (e.g. the
per-run git-ref namespace ``evo-harness-<HARNESS_VERSION>/<run-id>``)."""
from __future__ import annotations

HARNESS_VERSION: str = "v1"
