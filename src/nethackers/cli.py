"""``nethackers`` CLI: local evaluation (``eval``) and solution fetching
(``pull``).

``eval`` shells out to the pinned arena Docker image via
``nethackers.eval.runner.eval`` and prints the resulting ``Evidence`` as
JSON. ``pull`` fetches a solution repository pinned to an exact commit via
``nethackers.hubclient.pull.pull`` and prints the destination path. Both
subcommands are thin argument-parsing wrappers around those two functions;
tests/test_cli.py exercises this wiring with both monkeypatched, so no real
Docker or git process is ever invoked by the test suite.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from nethackers.contracts.models import DEFAULT_MAX_STEPS, Objective
from nethackers.eval.runner import eval as run_eval
from nethackers.hubclient.pull import pull


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nethackers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="Evaluate a solution against the pinned arena image.")
    e.add_argument("solution", help="Path to the solution directory (mounted read-only).")
    e.add_argument("--character", default=None, help="e.g. val-dwa-law-fem (default: random draw)")
    e.add_argument("--image", default="nethackers/arena:dev", help="Arena image to run.")
    e.add_argument("--seeds", default="0,1,2,3,4,5,6,7", help="Comma-separated trajectory ids.")
    e.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)

    pl = sub.add_parser("pull", help="Clone a solution repo pinned to an exact commit.")
    pl.add_argument("repo_at_commit", help="'owner/name@<sha>' or a full URL@<sha>.")
    pl.add_argument("dest", help="Destination directory for the clone.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "eval":
        objective = Objective(character=args.character, max_steps=args.max_steps, seed_set="cli")
        seed_ids = [int(s) for s in args.seeds.split(",")]
        evidence = run_eval(
            Path(args.solution), objective, args.image, seed_ids=seed_ids, now=_now()
        )
        print(json.dumps(evidence.to_dict(), indent=2))
        return 0

    if args.cmd == "pull":
        print(pull(args.repo_at_commit, Path(args.dest)))
        return 0

    return 1
