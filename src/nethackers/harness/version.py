"""Version of the FORMAT of what an evolve run publishes and records -- NOT the
harness software's version, and separate from the package version. It stamps
two things: the git-ref namespace winning solutions are published under
(``evo-harness-<RUN_SCHEMA_VERSION>/<run-id>``) and the ``run_schema_version``
field written into each run's ``run.json``. Bump it ONLY when that output
format changes incompatibly, so wins and records from different generations
stay resolvable and never collide. Bumping it implies no scoring change --
score parity is anchored to the evaluator image digest (recorded per atom),
not to this."""
from __future__ import annotations

RUN_SCHEMA_VERSION: str = "v1"
