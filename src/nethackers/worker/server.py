"""Placeholder ``nethackers-worker`` console entrypoint.

The held-out evaluation worker ships in Milestone 2. The script line exists now
so packaging is stable; invoking it raises a clear ``SystemExit`` rather than
failing to import.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> None:
    raise SystemExit("nethackers-worker ships in Milestone 2")
