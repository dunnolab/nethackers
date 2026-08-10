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
dispatches straight to one client method, then hands the raw response to
``nethackers.hubclient.output.emit`` (CLI-UX pass: rich renderers + ``-o``)
alongside two renderers: a ``rich`` one (``hubclient.render``, imported
here as ``rich_*``) and the baseline pure-Python one (``hubclient.client``,
imported here as ``plain_*``) -- ``emit`` itself picks which (if either)
actually runs, and falls back to raw ``json.dumps`` otherwise. ``--hub``
(default ``http://localhost:8000``, overridable via ``$NETHACKERS_HUB``)
and ``-o``/``--output`` (default ``"auto"``, overridable via
``$NETHACKERS_OUTPUT``; replaces the old boolean ``--json``) both live on a
shared parent parser (``_common_parser``) carried by every subcommand, so
either flag works whether given before or after the subcommand name --
e.g. both ``nethackers --hub URL board --objective random`` and
``nethackers board --objective random --hub URL`` work; argparse only
re-applies a parent's default when the attribute isn't already set on the
namespace, so whichever position actually supplies the flag wins.
tests/test_cli_m2a.py exercises this wiring with
``HubClient``/``register_solution`` monkeypatched, so no real HTTP/network
call is ever made by the test suite either.

All human chrome that isn't a data render -- the ``register`` device
flow's "visit this URL" prompt, the ``eval``/unknown-objective error --
goes to ``hubclient.output.err`` (a ``stderr``-bound ``rich`` ``Console``),
never stdout, so stdout stays machine-clean (in particular, exactly the
JSON payload and nothing else under ``-o json``) no matter what a
subcommand does along the way.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any

import httpx
from rich_argparse import RichHelpFormatter

from nethackers.eval.runner import eval_batch
from nethackers.harness.loop import run_loop
from nethackers.harness.operator import ClaudeOperator, CodexOperator
from nethackers.harness.store import LocalTreeStore
from nethackers.hub.objectives import CATALOG
from nethackers.hubclient.client import (
    HubClient,
    render_attainment as plain_attainment,
    render_board as plain_board,
    render_elites as plain_elites,
    render_search as plain_search,
    render_show as plain_show,
)
from nethackers.hubclient.output import emit, err
from nethackers.hubclient.pull import pull
from nethackers.hubclient.register import register_solution
from nethackers.hubclient.render import (
    render_attainment as rich_attainment,
    render_board as rich_board,
    render_elites as rich_elites,
    render_search as rich_search,
    render_show as rich_show,
)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _default_hub() -> str:
    return os.environ.get("NETHACKERS_HUB", "http://localhost:8000")


def _common_parser() -> argparse.ArgumentParser:
    """Parent parser carrying the flags every subcommand accepts: ``--hub``
    and ``-o``/``--output`` (both work whether given before or after the
    subcommand -- see module docstring). ``-o``/``--output`` selects
    ``hubclient.output.emit``'s format (``"auto"``/``"table"``/``"json"``/
    ``"plain"``; only meaningful for the hub-reading subcommands, harmless
    elsewhere) -- it replaces the old boolean ``--json``.

    Both flags default to ``argparse.SUPPRESS``, not a real value, quite
    deliberately: when a subparser (built with ``parents=[common]``) parses
    the tail of argv, it does so into a *fresh* namespace and then
    unconditionally copies every one of its own keys back onto the shared
    namespace the top-level parser already populated (``argparse``'s own
    ``_SubParsersAction.__call__``) -- so a REAL default here would clobber
    a top-level ``--hub``/``-o`` on every call, even when the subcommand
    itself never mentioned it. ``SUPPRESS`` means the subparser only ever
    contributes a ``hub``/``output`` key when the user actually typed the
    flag after the subcommand, so the top-level value (itself defaulted via
    ``_default_hub()``/``"auto"`` on ``_build_parser``'s own flags) survives
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
        "-o",
        "--output",
        choices=["auto", "table", "json", "plain"],
        default=argparse.SUPPRESS,
        help="Output format (default: the top-level -o/--output, itself "
        '"auto" or $NETHACKERS_OUTPUT).',
    )
    return common


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nethackers",
        formatter_class=RichHelpFormatter,
        description=(
            "NetHackers — a distributed effort to 'solve' NetHack by evolving "
            "deterministic symbolic players (built on AutoAscend), coordinated through "
            "a shared hub. This CLI browses the hub — the attainment map, ranking "
            "boards, and elite solutions — and lets you evaluate and register your own. "
            "Reads print a rich table in a terminal and JSON when piped; use -o to "
            "force a format."
        ),
        epilog=(
            "Env: NETHACKERS_HUB sets the hub URL; NETHACKERS_OUTPUT sets the default "
            "-o/--output format."
        ),
    )
    parser.add_argument(
        "--hub",
        default=_default_hub(),
        help="Hub API base URL (default: %(default)s; or $NETHACKERS_HUB).",
    )
    parser.add_argument(
        "-o",
        "--output",
        choices=["auto", "table", "json", "plain"],
        default="auto",
        help="Output format: auto (table on a terminal, json otherwise), "
        "table (rich), json (raw, jq-able), or plain (plain-text table). "
        "Default: %(default)s; or $NETHACKERS_OUTPUT.",
    )
    sub = parser.add_subparsers(dest="cmd")
    common = _common_parser()

    e = sub.add_parser(
        "eval", parents=[common], formatter_class=RichHelpFormatter,
        help="Evaluate a solution against a published objective's batch.",
    )
    e.add_argument("solution", help="Path to the solution directory (mounted read-only).")
    e.add_argument("--objective", required=True, help="A catalog objective name.")
    e.add_argument("--image", default="nethackers/arena:dev", help="Arena image to run.")

    evolve = sub.add_parser(
        "evolve", parents=[common], formatter_class=RichHelpFormatter,
        help="evolve a bot for an objective with a headless coding agent",
    )
    evolve.add_argument("objective")
    evolve.add_argument("--seed", required=True, help="seed solution root (e.g. roots/autoascend)")
    evolve.add_argument("--operator", choices=["codex", "claude"], default="claude")
    evolve.add_argument("--iterations", type=int, default=1)
    evolve.add_argument("--token-budget", type=int, default=200_000)
    evolve.add_argument("--timeout", type=float, default=1800.0)
    evolve.add_argument("--heldout-n", type=int, default=8)
    evolve.add_argument("--image", default="nethackers/arena:dev")
    evolve.add_argument("--token", default="dev-token")
    evolve.add_argument("--owner", default="dev")
    evolve.add_argument("--workdir", default=".nethackers/evolve")

    pl = sub.add_parser(
        "pull", parents=[common], formatter_class=RichHelpFormatter,
        help="Clone a solution repo pinned to an exact commit.",
    )
    pl.add_argument("repo_at_commit", help="'owner/name@<sha>' or a full URL@<sha>.")
    pl.add_argument("dest", help="Destination directory for the clone.")

    m = sub.add_parser(
        "map", aliases=["attainment"], parents=[common], formatter_class=RichHelpFormatter,
        help="Show attainment cells (identity x milestone).",
    )
    m.add_argument("--identity", default=None, help="Narrow to one identity (default: all).")

    el = sub.add_parser(
        "elites", parents=[common], formatter_class=RichHelpFormatter,
        help="Show the elite pool for an objective.",
    )
    el.add_argument("--objective", required=True, help="A catalog objective name.")

    b = sub.add_parser(
        "board", parents=[common], formatter_class=RichHelpFormatter,
        help="Show a ranking board.",
    )
    b.add_argument("--objective", default=None, help="A catalog objective name.")
    b.add_argument(
        "--metric", default=None, help="'coverage' or 'firsts' (instead of --objective)."
    )

    se = sub.add_parser(
        "search", parents=[common], formatter_class=RichHelpFormatter,
        help="Search registered solutions.",
    )
    se.add_argument("--owner", default=None, help="Narrow to one owner login.")
    se.add_argument("--limit", type=int, default=50)
    se.add_argument("--offset", type=int, default=0)

    sh = sub.add_parser(
        "show", parents=[common], formatter_class=RichHelpFormatter,
        help="Show one registered solution by digest.",
    )
    sh.add_argument("digest")

    r = sub.add_parser(
        "register", parents=[common], formatter_class=RichHelpFormatter,
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


def _unknown_objective(name: str) -> str:
    return (
        f"unknown objective {name!r}. Use 'random', 'all', or a full identity such as "
        f"'wiz-elf-cha-mal' (the hub catalog has {len(CATALOG)} objectives)."
    )


def _run(argv: list[str] | None) -> int:
    """Parse args and dispatch one subcommand. May raise -- ``main`` is the
    single place that turns any failure into a clean message, so nothing here
    needs its own try/except for hub I/O."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:  # bare `nethackers` -> friendly help (with the project description)
        parser.print_help()
        return 0

    if args.cmd == "eval":
        spec = CATALOG.get(args.objective)
        if spec is None:
            err.print(_unknown_objective(args.objective))
            return 2
        evidence = eval_batch(Path(args.solution), spec, args.image, now=_now())
        print(json.dumps(evidence.to_dict(), indent=2))
        return 0

    if args.cmd == "evolve":
        operator = {"codex": CodexOperator, "claude": ClaudeOperator}[args.operator]()
        results = run_loop(
            objective=args.objective, seed_tree=Path(args.seed),
            tree_store=LocalTreeStore(Path(args.workdir) / "trees"),
            operator=operator, hub=HubClient(args.hub), image=args.image,
            token=args.token, owner=args.owner, iterations=args.iterations,
            token_budget=args.token_budget, timeout_s=args.timeout,
            heldout_n=args.heldout_n, now_fn=_now, workdir=Path(args.workdir) / "work",
        )
        for i, r in enumerate(results):
            line = (f"iter {i}: {'✓ registered' if r.registered else '· ' + r.reason}"
                    f" dev={r.dev_fitness} held={r.heldout_fitness} tokens={r.tokens}")
            err.print(line)
        return 0

    if args.cmd == "pull":
        print(pull(args.repo_at_commit, Path(args.dest)))
        return 0

    if args.cmd in ("map", "attainment"):
        client = HubClient(args.hub)
        emit(client.attainment(args.identity), args.output,
             table=rich_attainment, plain=plain_attainment)
        return 0

    if args.cmd == "elites":
        if args.objective not in CATALOG:
            err.print(_unknown_objective(args.objective))
            return 2
        client = HubClient(args.hub)
        emit(client.elites(args.objective), args.output, table=rich_elites, plain=plain_elites)
        return 0

    if args.cmd == "board":
        if args.objective is not None and args.objective not in CATALOG:
            err.print(_unknown_objective(args.objective))
            return 2
        client = HubClient(args.hub)
        emit(client.board(args.objective, args.metric), args.output,
             table=rich_board, plain=plain_board)
        return 0

    if args.cmd == "search":
        client = HubClient(args.hub)
        emit(client.search(args.owner, args.limit, args.offset), args.output,
             table=rich_search, plain=plain_search)
        return 0

    if args.cmd == "show":
        client = HubClient(args.hub)
        emit(client.show(args.digest), args.output, table=rich_show, plain=plain_show)
        return 0

    if args.cmd == "register":
        client = HubClient(args.hub)
        reference = {"repo": args.repo, "commit": args.commit}
        manifest = _load_manifest(args)
        evidence = json.loads(Path(args.evidence).read_text())
        result = register_solution(
            hub=client, reference=reference, manifest=manifest, evidence=evidence,
            prompt=err.print,
        )
        print(json.dumps(result, indent=2))
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Runs the dispatch under a single top-level guard so no
    code path ever dumps a raw traceback at a user: known hub failures
    (unreachable / bad ``--hub`` URL / HTTP status) and any unexpected error
    become a one-line ``stderr`` message + a nonzero exit. Set
    ``NETHACKERS_DEBUG=1`` to re-raise and get the full traceback instead.
    (argparse usage errors raise ``SystemExit`` and pass straight through --
    they're already user-friendly.)"""
    try:
        return _run(argv)
    except KeyboardInterrupt:
        err.print("[yellow]aborted[/]")
        return 130
    except httpx.HTTPStatusError as exc:
        err.print(f"[red]hub error:[/] {exc.response.status_code} for {exc.request.url}")
        return 1
    except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
        err.print(
            f"[red]invalid hub URL[/]: {exc} — include a scheme, "
            "e.g. --hub http://localhost:8000"
        )
        return 2
    except httpx.HTTPError as exc:  # RequestError (connect/timeout/…) + any other httpx error
        target = getattr(getattr(exc, "request", None), "url", None)
        where = f" ({target})" if target else ""
        err.print(f"[red]cannot reach the hub[/]{where} — is it running? (docker compose up -d)")
        return 1
    except Exception as exc:  # never surface a raw traceback to a user
        if os.environ.get("NETHACKERS_DEBUG"):
            raise
        err.print(f"[red]nethackers: unexpected error[/]: {type(exc).__name__}: {exc}")
        err.print("[dim](set NETHACKERS_DEBUG=1 for the full traceback)[/]")
        return 1
