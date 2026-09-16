"""The registered coding agents nethackers can drive — THE single source of
the operator set. Every surface that enumerates it (the CLI ``--operator``
choices, the TUI operator picker, the ``doctor`` operator-readiness check)
derives from ``OPERATORS``, so registering a new agent is one edit here plus
its adapter in ``discovery.py``/``container_operator.py``.

Per-agent BEHAVIOUR (the login-status probe, the argv builder, the model
catalogue) legitimately branches per agent and lives with those adapters,
keyed off these names; this module owns only the SET and the default, so the
set can never again drift between an argparse ``choices=`` list, a TUI picker,
and a readiness check the way it did before it was centralized here."""
from __future__ import annotations

OPERATORS: tuple[str, ...] = ("codex", "claude", "opencode2")

# The single interactive default (evolve's operator, the TUI picker's initial
# value). Not every command shares it -- e.g. `models` defaults to codex -- but
# where "the default coding agent" is meant, it means this.
DEFAULT_OPERATOR: str = "claude"
