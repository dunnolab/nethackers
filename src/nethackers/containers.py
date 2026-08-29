"""Names + a label for every container nethackers starts, so
`docker ps -f name=nethackers-` (and `-f label=nethackers`) list them and
cleanup is one command. See spec §5.13 / D14 / INV13. Leaf module: stdlib only."""
from __future__ import annotations

import secrets

NETHACKERS_LABEL = "nethackers"


def container_name(role: str) -> str:
    """`nethackers-<role>-<hex>` — the hex keeps it unique among running
    containers (evals/probes run concurrently; a `--name` must be unique)."""
    return f"nethackers-{role}-{secrets.token_hex(4)}"


def label_args() -> list[str]:
    """The `docker run` args that stamp the shared `nethackers` label."""
    return ["--label", NETHACKERS_LABEL]
