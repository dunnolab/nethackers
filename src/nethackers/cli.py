"""``nethackers`` CLI: local evaluation (``eval``), solution fetching
(``pull``), and the M2a hub-facing subcommands (``map``/``attainment``,
``elites``, ``board``, ``search``, ``show``, ``register``).

``eval`` resolves ``--objective <name>`` against the published hub catalog
(``nethackers.hub.objectives.CATALOG`` -- importable standalone, no
``fastapi``/hub-server dependency) and shells out to the pinned arena
Docker image for that objective's whole batch via
``nethackers.eval.runner.eval_batch``, printing the resulting ``Evidence``
as JSON -- this is the only supported ``eval`` path (the legacy single-
character ``--character``/``--seeds`` path has been retired; see
eval/runner.py). ``pull`` fetches a solution repository pinned to an exact
commit via ``nethackers.hubclient.pull.pull`` and prints the destination
path. Both are thin argument-parsing wrappers; tests/test_cli.py exercises
this wiring with both monkeypatched, so no real Docker or git process is
ever invoked by the test suite.

The hub-facing subcommands are thin wrappers the same way, but over
``nethackers.hubclient.client.HubClient`` (reads + register) and
``nethackers.hubclient.register.register_solution`` (the GitHub device
flow) instead -- every one of them builds a ``HubClient(args.hub)`` and
dispatches straight to one client method, then prints either that method's
raw JSON response (``--json``, valid for ``jq``) or a beautified render
from one of the ``render_*`` functions in ``hubclient.client``. ``--hub``
(default ``http://localhost:8000``, overridable via ``$NETHACKERS_HUB``)
and ``--json`` both live on a shared parent parser (``_common_parser``)
carried by every subcommand, so either flag works whether given before or
after the subcommand name -- e.g. both ``nethackers --hub URL board
--objective random`` and ``nethackers board --objective random --hub URL``
work; argparse only re-applies a parent's default when the attribute isn't
already set on the namespace, so whichever position actually supplies the
flag wins. tests/test_cli_m2a.py exercises this wiring with
``HubClient``/``register_solution`` monkeypatched, so no real HTTP/network
call is ever made by the test suite either.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nethackers.eval.runner import eval_batch
from nethackers.hub.objectives import CATALOG
from nethackers.hubclient.client import (
    HubClient,
    render_attainment,
    render_board,
    render_elites,
    render_search,
    render_show,
)
from nethackers.hubclient.pull import pull
from nethackers.hubclient.register import register_solution


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _default_hub() -> str:
    return os.environ.get("NETHACKERS_HUB", "http://localhost:8000")


def _common_parser() -> argparse.ArgumentParser:
    """Parent parser carrying the flags every subcommand accepts: ``--hub``
    (so it works whether given before or after the subcommand -- see module
    docstring) and ``--json`` (raw hub JSON instead of the beautified
    render; only meaningful for the hub-reading subcommands, harmless
    elsewhere).

    ``--hub``'s default is ``argparse.SUPPRESS``, not ``_default_hub()``,
    quite deliberately: when a subparser (built with ``parents=[common]``)
    parses the tail of argv, it does so into a *fresh* namespace and then
    unconditionally copies every one of its own keys back onto the shared
    namespace the top-level parser already populated (``argparse``'s own
    ``_SubParsersAction.__call__``) -- so a REAL default here would clobber
    a top-level ``--hub`` on every call, even when the subcommand itself
    never mentioned ``--hub``. ``SUPPRESS`` means the subparser only ever
    contributes a ``hub`` key when the user actually typed ``--hub`` after
    the subcommand, so the top-level value (itself defaulted via
    ``_default_hub()`` on ``_build_parser``'s own ``--hub``) survives
    untouched otherwise -- giving exactly "subcommand-level wins if given,
    else the top-level value" without hand-rolling the merge.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--hub",
        default=argparse.SUPPRESS,
        help="Hub API base URL (default: the top-level --hub, itself "
        "http://localhost:8000 or $NETHACKERS_HUB).",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Print the raw hub JSON response instead of a beautified render.",
    )
    return common


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nethackers")
    parser.add_argument(
        "--hub",
        default=_default_hub(),
        help="Hub API base URL (default: %(default)s; or $NETHACKERS_HUB).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = _common_parser()

    e = sub.add_parser(
        "eval", parents=[common],
        help="Evaluate a solution against a published objective's batch.",
    )
    e.add_argument("solution", help="Path to the solution directory (mounted read-only).")
    e.add_argument("--objective", required=True, help="A catalog objective name.")
    e.add_argument("--image", default="nethackers/arena:dev", help="Arena image to run.")

    pl = sub.add_parser(
        "pull", parents=[common], help="Clone a solution repo pinned to an exact commit."
    )
    pl.add_argument("repo_at_commit", help="'owner/name@<sha>' or a full URL@<sha>.")
    pl.add_argument("dest", help="Destination directory for the clone.")

    m = sub.add_parser(
        "map", aliases=["attainment"], parents=[common],
        help="Show attainment cells (identity x milestone).",
    )
    m.add_argument("--identity", default=None, help="Narrow to one identity (default: all).")

    el = sub.add_parser(
        "elites", parents=[common], help="Show the elite pool for an objective."
    )
    el.add_argument("--objective", required=True, help="A catalog objective name.")

    b = sub.add_parser("board", parents=[common], help="Show a ranking board.")
    b.add_argument("--objective", default=None, help="A catalog objective name.")
    b.add_argument(
        "--metric", default=None, help="'coverage' or 'firsts' (instead of --objective)."
    )

    se = sub.add_parser("search", parents=[common], help="Search registered solutions.")
    se.add_argument("--owner", default=None, help="Narrow to one owner login.")
    se.add_argument("--limit", type=int, default=50)
    se.add_argument("--offset", type=int, default=0)

    sh = sub.add_parser(
        "show", parents=[common], help="Show one registered solution by digest."
    )
    sh.add_argument("digest")

    r = sub.add_parser(
        "register", parents=[common],
        help="Register a solution with the hub via the GitHub device flow.",
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


def _emit(response: Any, render_fn: Callable[[Any], str], as_json: bool) -> None:
    """Print a hub read response either raw (``--json``, valid for ``jq``)
    or through ``render_fn`` -- the beautified human render."""
    if as_json:
        print(json.dumps(response, indent=2))
    else:
        print(render_fn(response))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "eval":
        spec = CATALOG.get(args.objective)
        if spec is None:
            print(f"nethackers eval: unknown objective {args.objective!r}", file=sys.stderr)
            return 2
        evidence = eval_batch(Path(args.solution), spec, args.image, now=_now())
        print(json.dumps(evidence.to_dict(), indent=2))
        return 0

    if args.cmd == "pull":
        print(pull(args.repo_at_commit, Path(args.dest)))
        return 0

    if args.cmd in ("map", "attainment"):
        client = HubClient(args.hub)
        _emit(client.attainment(args.identity), render_attainment, args.json)
        return 0

    if args.cmd == "elites":
        client = HubClient(args.hub)
        _emit(client.elites(args.objective), render_elites, args.json)
        return 0

    if args.cmd == "board":
        client = HubClient(args.hub)
        _emit(client.board(args.objective, args.metric), render_board, args.json)
        return 0

    if args.cmd == "search":
        client = HubClient(args.hub)
        _emit(client.search(args.owner, args.limit, args.offset), render_search, args.json)
        return 0

    if args.cmd == "show":
        client = HubClient(args.hub)
        _emit(client.show(args.digest), render_show, args.json)
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
