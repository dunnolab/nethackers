"""``nethackers`` CLI: local evaluation (``eval``), solution fetching
(``pull``), and the M2a hub-facing subcommands (``map``/``attainment``,
``elites``, ``board``, ``search``, ``show``, ``register``).

``eval`` shells out to the pinned arena Docker image via
``nethackers.eval.runner.eval`` and prints the resulting ``Evidence`` as
JSON. ``pull`` fetches a solution repository pinned to an exact commit via
``nethackers.hubclient.pull.pull`` and prints the destination path. Both are
thin argument-parsing wrappers around those two functions;
tests/test_cli.py exercises this wiring with both monkeypatched, so no real
Docker or git process is ever invoked by the test suite.

The hub-facing subcommands are thin wrappers the same way, but over
``nethackers.hubclient.client.HubClient`` (reads + register) and
``nethackers.hubclient.register.register_solution`` (the GitHub device
flow) instead -- every one of them builds a ``HubClient(args.hub)`` and
dispatches straight to one client method, then prints either that method's
JSON response or (for ``map``/``board``) an ASCII table rendered by
``render_attainment``/``render_board``. ``--hub`` (default
``http://localhost:8000``, overridable via ``$NETHACKERS_HUB`` or the
``--hub`` flag itself) selects the hub these subcommands talk to.
tests/test_cli_m2a.py exercises this wiring with ``HubClient``/
``register_solution`` monkeypatched, so no real HTTP/network call is ever
made by the test suite either.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any

from nethackers.contracts.models import DEFAULT_MAX_STEPS, Objective
from nethackers.eval.runner import eval as run_eval
from nethackers.hubclient.client import HubClient, render_attainment, render_board
from nethackers.hubclient.pull import pull
from nethackers.hubclient.register import register_solution


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nethackers")
    parser.add_argument(
        "--hub",
        default=os.environ.get("NETHACKERS_HUB", "http://localhost:8000"),
        help="Hub API base URL (default: %(default)s; or $NETHACKERS_HUB).",
    )
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

    m = sub.add_parser(
        "map", aliases=["attainment"], help="Show attainment cells (identity x milestone)."
    )
    m.add_argument("--identity", default=None, help="Narrow to one identity (default: all).")

    el = sub.add_parser("elites", help="Show the elite pool for an objective.")
    el.add_argument("--objective", required=True, help="A catalog objective name.")

    b = sub.add_parser("board", help="Show a ranking board.")
    b.add_argument("--objective", default=None, help="A catalog objective name.")
    b.add_argument(
        "--metric", default=None, help="'coverage' or 'firsts' (instead of --objective)."
    )

    se = sub.add_parser("search", help="Search registered solutions.")
    se.add_argument("--owner", default=None, help="Narrow to one owner login.")
    se.add_argument("--limit", type=int, default=50)
    se.add_argument("--offset", type=int, default=0)

    sh = sub.add_parser("show", help="Show one registered solution by digest.")
    sh.add_argument("digest")

    r = sub.add_parser(
        "register", help="Register a solution with the hub via the GitHub device flow."
    )
    r.add_argument("--repo", required=True, help="e.g. github.com/owner/name")
    r.add_argument("--commit", required=True, help="40-hex commit sha")
    manifest_source = r.add_mutually_exclusive_group(required=True)
    manifest_source.add_argument(
        "--solution", default=None, help="Solution dir containing nethackers.solution.json."
    )
    manifest_source.add_argument(
        "--manifest", default=None, help="Path to a manifest JSON file directly."
    )
    r.add_argument("--evidence", required=True, help="Path to a prior `eval` JSON output file.")

    return parser


def _load_manifest(args: argparse.Namespace) -> dict[str, Any]:
    """``--manifest <file>`` read directly, or ``--solution <dir>``'s
    ``nethackers.solution.json`` -- ``register``'s argparse mutually
    exclusive group guarantees exactly one of the two is set."""
    if args.manifest:
        path = Path(args.manifest)
    else:
        path = Path(args.solution) / "nethackers.solution.json"
    return json.loads(path.read_text())


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

    if args.cmd in ("map", "attainment"):
        client = HubClient(args.hub)
        print(render_attainment(client.attainment(args.identity)))
        return 0

    if args.cmd == "elites":
        client = HubClient(args.hub)
        print(json.dumps(client.elites(args.objective), indent=2))
        return 0

    if args.cmd == "board":
        client = HubClient(args.hub)
        print(render_board(client.board(args.objective, args.metric)))
        return 0

    if args.cmd == "search":
        client = HubClient(args.hub)
        print(json.dumps(client.search(args.owner, args.limit, args.offset), indent=2))
        return 0

    if args.cmd == "show":
        client = HubClient(args.hub)
        print(json.dumps(client.show(args.digest), indent=2))
        return 0

    if args.cmd == "register":
        client = HubClient(args.hub)
        reference = {"repo": args.repo, "commit": args.commit}
        manifest = _load_manifest(args)
        evidence = json.loads(Path(args.evidence).read_text())
        result = register_solution(
            hub=client, reference=reference, manifest=manifest, evidence=evidence
        )
        print(json.dumps(result, indent=2))
        return 0

    return 1
