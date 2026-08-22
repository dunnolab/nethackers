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
``nethackers.hubclient.client.HubClient`` (reads + register) instead --
every one of them builds a ``HubClient(args.hub)`` and dispatches straight
to one client method, then hands the raw response to
``nethackers.hubclient.output.emit`` (CLI-UX pass: rich renderers + ``-o``)
alongside two renderers: a ``rich`` one (``hubclient.render``, imported
here as ``rich_*``) and the baseline pure-Python one (``hubclient.client``,
imported here as ``plain_*``) -- ``emit`` itself picks which (if either)
actually runs, and falls back to raw ``json.dumps`` otherwise. ``--hub``
(default ``https://nethackers.dunnolab.ai``, overridable via ``$NETHACKERS_HUB``)
and ``-o``/``--output`` (default ``"auto"``, overridable via
``$NETHACKERS_OUTPUT``; replaces the old boolean ``--json``) both live on a
shared parent parser (``_common_parser``) carried by every subcommand, so
either flag works whether given before or after the subcommand name --
e.g. both ``nethackers --hub URL board --objective random`` and
``nethackers board --objective random --hub URL`` work; argparse only
re-applies a parent's default when the attribute isn't already set on the
namespace, so whichever position actually supplies the flag wins.
tests/test_cli_m2a.py exercises this wiring with ``HubClient``
monkeypatched, so no real HTTP/network call is ever made by the test suite
either; ``login``/``register``'s credential handling is covered by
tests/test_cli_login.py.

All human chrome that isn't a data render -- the ``login`` device
flow's "visit this URL" prompt, the ``eval``/unknown-objective error --
goes to ``hubclient.output.err`` (a ``stderr``-bound ``rich`` ``Console``),
never stdout, so stdout stays machine-clean (in particular, exactly the
JSON payload and nothing else under ``-o json``) no matter what a
subcommand does along the way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich_argparse import RichHelpFormatter

from nethackers.eval.runner import eval_batch
from nethackers.harness.discovery import ModelInfo, list_models, preflight_model
from nethackers.harness.launch import EvolveParams, _now, prepare_evolve
from nethackers.hub.objectives import CATALOG
from nethackers.hubclient import credentials as _cred
from nethackers.hubclient.client import (
    HubClient,
    _short_digest,
    plain_frontier,
    render_board as plain_board,
    render_elites as plain_elites,
    render_search as plain_search,
    render_show as plain_show,
)
from nethackers.hubclient.credentials import Credentials, whoami_from_token
from nethackers.hubclient.frontier import champion, champion_scores, overall_mean, universe_scores
from nethackers.hubclient.live import EpisodeStream
from nethackers.hubclient.output import emit, err
from nethackers.hubclient.publish import PublishError, ensure_repo, gh_login, publish_solution
from nethackers.hubclient.pull import pull
from nethackers.hubclient.register import device_login, refresh_access_token
from nethackers.hubclient.render import (
    render_board as rich_board,
    render_elites as rich_elites,
    render_frontier_grid,
    render_search as rich_search,
    render_show as rich_show,
)
from nethackers.tui.app import NetHackersApp

# Time seam: tests monkeypatch ``cli._time_now`` to make credential
# expiry/refresh deterministic (avoids a wall-clock ``time.time()`` read).
_time_now = time.time


def _default_hub() -> str:
    return os.environ.get("NETHACKERS_HUB", "https://nethackers.dunnolab.ai")


def _login_prompt(verification_uri: str, user_code: str) -> None:
    """Styled device-flow prompt: the URL and the code on their own lines in a
    bordered panel, printed to stderr so ``-o json`` / pipes stay clean."""
    body = Text()
    body.append("1  Open this URL in your browser\n", style="dim")
    body.append(f"     {verification_uri}\n\n", style="bold cyan")
    body.append("2  Enter this code\n", style="dim")
    body.append(f"     {user_code}", style="bold yellow")
    err.print(Panel(body, title="[b green]Authorize NetHackers[/]",
                    border_style="green", expand=False, padding=(1, 2)))


def _load_creds() -> Credentials | None:
    return _cred.load()


def _authed_token() -> str | None:
    """Return a usable access token from the stored credential, refreshing it
    silently when it has expired and a refresh token is on hand.

    Returns ``None`` when there is no stored credential at all (the caller
    prints the "run ``nethackers login``" hint). Otherwise: if the credential
    is expired and carries a ``refresh_token``, exchange it for a fresh token
    set via ``refresh_access_token``, persist the rebuilt credential (with a
    recomputed ``expires_at``), and return the new access token; if it is
    still valid (or no refresh token is available), return the stored access
    token as-is."""
    creds = _cred.load()
    if creds is None:
        return None
    if creds.is_expired(_time_now()) and creds.refresh_token:
        tok = refresh_access_token(creds.refresh_token)
        creds = Credentials(
            creds.login,
            tok["access_token"],
            tok["refresh_token"],
            (_time_now() + tok["expires_in"]) if tok["expires_in"] else None,
        )
        _cred.save(creds)
        return creds.access_token
    return creds.access_token


def _models_table(operator: str, rows: list[dict[str, Any]]):
    from rich.table import Table
    table = Table(title=f"{operator} models", title_style="bold")
    table.add_column("id")
    table.add_column("label")
    table.add_column("effort")
    for r in rows:
        name = r["id"] + (" [dim](deprecated)[/]" if r["deprecated"] else "")
        # str() each item defensively: a display command must never crash on an
        # unexpected reasoning shape (see discovery._codex_reasoning).
        table.add_row(name, r["label"], ", ".join(str(x) for x in r["reasoning"]))
    return table


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
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Never open the interactive TUI; print help/plain output.",
    )
    sub = parser.add_subparsers(dest="cmd")
    common = _common_parser()

    sub.add_parser(
        "login", parents=[common], formatter_class=RichHelpFormatter,
        help="Authenticate via the GitHub device flow and store the resulting credentials.",
    )
    sub.add_parser(
        "logout", parents=[common], formatter_class=RichHelpFormatter,
        help="Clear stored credentials.",
    )
    sub.add_parser(
        "whoami", parents=[common], formatter_class=RichHelpFormatter,
        help="Show the currently logged-in identity.",
    )

    e = sub.add_parser(
        "eval", parents=[common], formatter_class=RichHelpFormatter,
        help="Evaluate a solution against a published objective's batch.",
    )
    e.add_argument("solution", help="Path to the solution directory (mounted read-only).")
    e.add_argument("--objective", required=True, help="A catalog objective name.")
    e.add_argument("--image", default="nethackers/arena:dev", help="Arena image to run.")
    e.add_argument(
        "--max-parallel-evals", type=int, default=8,
        help="Cap on episodes the arena runs concurrently (default: %(default)s).",
    )

    mo = sub.add_parser(
        "models", parents=[common], formatter_class=RichHelpFormatter,
        help="List the models the installed operator CLI can actually serve on this machine.",
    )
    mo.add_argument("--operator", choices=["codex", "claude"], default="codex")

    evolve = sub.add_parser(
        "evolve", parents=[common], formatter_class=RichHelpFormatter,
        help="Evolve a bot for an objective with a headless coding agent.",
    )
    evolve.add_argument("objective")
    evolve.add_argument("--seed", required=True, help="seed solution root (e.g. roots/autoascend)")
    evolve.add_argument(
        "--select-k", type=int, default=1,
        help="Sample among the top-k trusted elites (1 = exploit/argmax, default: %(default)s).",
    )
    evolve.add_argument(
        "--select-temp", type=float, default=1.0,
        help="Softmax temperature for --select-k > 1 (default: %(default)s).",
    )
    evolve.add_argument(
        "--from-seed", action="store_true",
        help="Ignore the hub; cold-start from --seed.",
    )
    evolve.add_argument(
        "--no-migrate", action="store_true",
        help="Disable mid-run migration: don't adopt a better hub elite between "
             "iterations (keep a pure single-parent lineage).")
    evolve.add_argument("--operator", choices=["codex", "claude"], default="claude")
    evolve.add_argument(
        "--model", default=None,
        help="Pin the operator's model (e.g. claude-opus-5, gpt-5.6-sol); "
        "default: the harness's own default.",
    )
    evolve.add_argument(
        "--effort", default=None,
        help="Reasoning effort: low|medium|high|xhigh|max (codex also 'ultra'); "
        "default: the harness's own default.",
    )
    evolve.add_argument("--iterations", type=int, default=1)
    evolve.add_argument("--validation-n", type=int, default=15)
    evolve.add_argument(
        "--max-parallel-evals", type=int, default=8,
        help="Cap on episodes the arena runs concurrently per eval (default: %(default)s).",
    )
    evolve.add_argument("--image", default="nethackers/arena:dev")
    evolve.add_argument(
        "--token", default=None,
        help="Attribution token (default: stored `nethackers login` credentials, "
        "else 'dev-token').",
    )
    evolve.add_argument(
        "--owner", default=None,
        help="Attribution owner (default: stored `nethackers login` credentials, else 'dev').",
    )
    evolve.add_argument("--workdir", default=str(Path.home() / ".nethackers" / "evolve"))
    evolve.add_argument(
        "--run-name", default=None,
        help="Optional label appended to the run-id folder under runs/.",
    )

    pl = sub.add_parser(
        "pull", parents=[common], formatter_class=RichHelpFormatter,
        help="Clone a solution repo pinned to an exact commit.",
    )
    pl.add_argument("repo_at_commit", help="'owner/name@<sha>' or a full URL@<sha>.")
    pl.add_argument("dest", help="Destination directory for the clone.")

    m = sub.add_parser(
        "frontier", aliases=["map", "attainment"], parents=[common],
        formatter_class=RichHelpFormatter,
        help="Show the frontier — how far the community has collectively "
        "reached, as a role x variation number grid.",
    )
    m.add_argument(
        "--program", nargs="?", const="", default=None,
        help="Show one program across all identities (default: the champion). "
        "Pass a digest to pick a specific solution.",
    )

    el = sub.add_parser(
        "elites", parents=[common], formatter_class=RichHelpFormatter,
        help="Show the elite pool for an objective.",
    )
    el.add_argument("--objective", required=True, help="A catalog objective name.")

    b = sub.add_parser(
        "leaderboard", aliases=["board"], parents=[common],
        formatter_class=RichHelpFormatter,
        help="Show the leaderboard — solutions ranked on an objective.",
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
        help="Register a repo@commit solution link with the hub (uses your stored login).",
    )
    r.add_argument("--repo", required=True, help="e.g. github.com/owner/name")
    r.add_argument("--commit", required=True, help="40-hex commit sha")
    r.add_argument(
        "--root", default="",
        help="Path to the solution within the repo (default: the repo root).",
    )

    sm = sub.add_parser(
        "submit", parents=[common], formatter_class=RichHelpFormatter,
        help="Publish a solution to your public <you>/nethacker repo (via gh) and register it.",
    )
    sm.add_argument("solution_dir", help="Path to the solution directory to publish.")
    sm.add_argument(
        "--repo-name", default="nethacker",
        help="Repo under your account to publish into (default: nethacker).",
    )
    sm.add_argument(
        "--message", default="nethackers submit",
        help="Commit message for the published solution.",
    )

    return parser


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

    if args.cmd is None:
        # bare `nethackers`: on a TTY (and not --no-tui), open the dashboard
        # -- agents and pipes get the same friendly help as every other
        # invocation (with the project description), never Textual escape
        # codes down a pipe.
        if sys.stdout.isatty() and not args.no_tui:
            app = NetHackersApp(hub=args.hub, creds=_load_creds())
            app.run()
            if app.error is not None:
                raise app.error  # let main()'s top-level guard render it
            return 0
        parser.print_help()
        return 0

    if args.cmd == "login":
        tok = device_login(prompt=_login_prompt)
        login = whoami_from_token(tok["access_token"])
        _cred.save(Credentials(
            login=login,
            access_token=tok["access_token"],
            refresh_token=tok["refresh_token"],
            expires_at=(_time_now() + tok["expires_in"]) if tok["expires_in"] else None,
        ))
        err.print(f"logged in as [b]@{login}[/]")
        return 0

    if args.cmd == "logout":
        _cred.clear()
        err.print("logged out")
        return 0

    if args.cmd == "whoami":
        c = _load_creds()
        if c is None:
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        if args.output == "json":
            print(json.dumps({"login": c.login}))
        else:
            err.print(f"[b]@{c.login}[/]")
        return 0

    if args.cmd == "eval":
        spec = CATALOG.get(args.objective)
        if spec is None:
            err.print(_unknown_objective(args.objective))
            return 2
        evidence = eval_batch(
            Path(args.solution), spec, args.image, now=_now(),
            max_parallel_evals=args.max_parallel_evals,
        )
        print(json.dumps(evidence.to_dict(), indent=2))
        return 0

    if args.cmd == "models":
        models: list[ModelInfo] | None = list_models(args.operator)
        if models is None:
            err.print(f"[yellow]couldn't determine {args.operator}'s models[/] "
                      "(offline, old CLI, or logged out) — check `"
                      f"{args.operator} --version` / login.")
            models = []
        data = [{"id": m.id, "label": m.label, "reasoning": list(m.reasoning),
                 "deprecated": m.deprecated} for m in models]
        emit(data, args.output,
             table=lambda rows: _models_table(args.operator, rows),
             plain=lambda rows: "\n".join(r["id"] for r in rows))
        return 0

    if args.cmd == "evolve":
        _creds = _load_creds()
        # SELECT (compounding from the hub's top trusted elite) + run.json +
        # run wiring all live in prepare_evolve, shared with the in-app form.
        params = EvolveParams(
            objective=args.objective, seed=str(args.seed), operator=args.operator,
            iterations=args.iterations, validation_n=args.validation_n,
            migrate=not args.no_migrate, max_parallel_evals=args.max_parallel_evals,
            image=args.image, hub=args.hub, workdir=args.workdir, run_name=args.run_name,
            token=args.token or (_creds.access_token if _creds else "dev-token"),
            owner=args.owner or (_creds.login if _creds else "dev"),
            from_seed=args.from_seed, select_k=args.select_k, select_temp=args.select_temp,
            model=args.model, effort=args.effort,
        )

        # Preflight only when a model is pinned: harness-default has nothing to
        # validate, and this keeps the model=None path (the common case + every
        # existing wiring test) free of any CLI/network probe. A confident
        # refuse stops here -- no run dir, no doomed spin; unknown only warns.
        if args.model:
            pf = preflight_model(args.operator, args.model)
            if pf.action == "refuse":
                err.print(f"[red]{pf.message}[/]")
                return 2
            if pf.action == "warn":
                err.print(f"[yellow]{pf.message}[/]")
        plan = prepare_evolve(params)

        if sys.stdout.isatty() and args.output != "json" and not args.no_tui:
            # Interactive session: auto-start the run + open its monitor over
            # the dashboard; esc roams other tabs while it runs, quit stops it.
            app = NetHackersApp(hub=args.hub, creds=_creds, start="runs", evolve=plan)
            app.run()
            if app.error is not None:
                raise app.error  # let main()'s friendly hub/docker handlers fire
            return 0

        # Headless: run to completion + print the summary.
        t0 = time.monotonic()
        err.print(
            f"evolving [b]{args.objective}[/] · operator={args.operator} · "
            f"{args.iterations} iter"
        )
        with Live(console=err, auto_refresh=False, transient=False) as live:
            stream = EpisodeStream(live)
            results = plan.run(
                {"on_state": lambda s: None, "on_episode": stream.on_episode,
                 "on_log": lambda tag, line: None},
                report=lambda m: live.console.print(
                    f"{time.monotonic() - t0:7.1f}s  {m}", markup=False),
            )
            stream.finish()
        n_reg = sum(1 for r in results if r.registered)
        err.print(f"done · [b]{n_reg}[/]/{len(results)} iteration(s) registered a new elite")
        return 0

    if args.cmd == "pull":
        print(pull(args.repo_at_commit, Path(args.dest)))
        return 0

    if args.cmd in ("frontier", "map", "attainment"):
        client = HubClient(args.hub)
        note = ""
        scores: dict[str, float | None]
        if args.program is not None:
            digest = args.program or None
            if digest is None:
                champ = champion(client)
                if champ is None:
                    emit(
                        {}, args.output,
                        table=lambda s: Text("no ranked programs yet.", style="dim"),
                        plain=lambda s: "no ranked programs yet.",
                    )
                    return 0
                digest, owner = champ
                note = f"@{owner}/{_short_digest(digest)} — this one program across all identities"
            scores = dict(champion_scores(client, digest))
        else:
            scores = dict(universe_scores(client))
        om = overall_mean(scores)
        if om is not None:
            note = f"{note} · overall {om:.2f}" if note else f"overall {om:.2f}"
        emit(
            scores, args.output,
            table=lambda s: render_frontier_grid(s, note=note),
            plain=plain_frontier,
        )
        return 0

    if args.cmd == "elites":
        if args.objective not in CATALOG:
            err.print(_unknown_objective(args.objective))
            return 2
        client = HubClient(args.hub)
        emit(client.elites(args.objective), args.output, table=rich_elites, plain=plain_elites)
        return 0

    if args.cmd in ("leaderboard", "board"):
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
        token = _authed_token()
        if token is None:
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        result = HubClient(args.hub).register(
            token=token, repo=args.repo, commit=args.commit, root=args.root,
        )
        emit(
            result, args.output,
            table=lambda r: Text(f"registered {r['solution_id']}", style="green"),
            plain=lambda r: f"registered {r['solution_id']}",
        )
        return 0

    if args.cmd == "submit":
        creds = _load_creds()
        if creds is None:
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        gh = gh_login()
        if gh is None:
            err.print("[yellow]gh unavailable[/] — install the GitHub CLI and run `gh auth login`")
            return 1
        if gh != creds.login:
            err.print(f"gh is authed as [b]@{gh}[/] but you're logged in as "
                      f"[b]@{creds.login}[/] — sign in to the same account")
            return 1
        token = _authed_token()
        if token is None:  # unreachable (creds is set) -- narrows for the type checker
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        slug = f"{creds.login}/{args.repo_name}"
        try:
            ensure_repo(slug)
            sha = publish_solution(args.solution_dir, slug, message=args.message)
        except PublishError as e:
            err.print(f"[red]publish failed[/] — {e}")
            return 1
        result = HubClient(args.hub).register(
            token=token, repo=f"github.com/{slug}", commit=sha, root="",
        )
        emit(
            result, args.output,
            table=lambda r: Text(f"submitted {r['solution_id']}", style="green"),
            plain=lambda r: f"submitted {r['solution_id']}",
        )
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
