"""The one-shot genesis command: archive the hub's live PUBLIC tables and
recreate them empty (design 2026-09-14, Sec 5.5 / D3 / D12).

Run against the DEPLOYED hub, after a database backup, immediately after the
arena-major bump goes out -- step 5 of that design's rollout, so the window in
which the board still shows major-1 numbers nobody can add to stays short.
Deliberately NOT a boot migration (D12): this empties the live board, and hub
deploys auto-roll-back on a failed health check, which would otherwise leave
archived tables behind code that knows nothing about them.

Deliberately not a ``nethackers`` CLI subcommand either. That CLI is the
contributor-facing client: it reaches the hub only over HTTP and has no access
to the sqlite file at all (the hub reads ``NETHACKERS_DB``, ``/data/hub.db`` in
the container). Shipping a destructive hub-maintenance command to every PyPI
user would be worse than useless to them. This follows
``hub/baseline_compute.py`` -- the hub's existing operator entry point -- and is
run the same way, by the hub image's own python against the mounted database:

    python -m nethackers.hub.genesis --db /data/hub.db

Unlike ``baseline_compute``, it does NOT call ``init_schema()`` first, and it
checks the file exists before opening it at all. A typo'd ``--db`` would
otherwise create an empty database at the wrong path and report a successful
all-zero genesis over it. Opening a sqlite connection is itself creative, so
the existence check has to come BEFORE ``Store(...)`` -- leaving the check to
the first query would still litter a stray empty file, and would surface as a
raw ``OperationalError`` traceback rather than a sentence and an exit code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nethackers.hub.store import Store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m nethackers.hub.genesis",
        description="Archive the live public tables (solutions, atoms, "
                    "baseline_atoms, lineage, attainment, attainment_holders) "
                    "to *_v1 and recreate them empty. Empties the live board; "
                    "back the database up first. Idempotent: a second run is a "
                    "no-op.",
    )
    p.add_argument("--db", required=True, help="path to the hub sqlite db")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not Path(args.db).is_file():
        print(f"genesis: no database at {args.db} — nothing was created or changed.\n"
              f"Point --db at the hub's sqlite file (in the container: /data/hub.db).",
              file=sys.stderr)
        return 2
    counts = Store(args.db).genesis()
    if not counts:
        print("genesis: already applied, nothing to do")
        return 0
    for table, rows in sorted(counts.items()):
        print(f"archived {rows:>8} rows  {table} -> {table}_v1")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wiring over genesis()
    raise SystemExit(main())
