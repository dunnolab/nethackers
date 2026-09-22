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
e.g. both ``nethackers --hub URL board --scope generalist`` and
``nethackers board --scope generalist --hub URL`` work; argparse only
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

The top-level ``--version`` flag is handled in ``_run`` before any of the
above -- before the stage announcement, subcommand dispatch, or the bare-
TUI launch -- since it's a self-contained, offline diagnostic (reads the
package version + ``harness.version.RUN_SCHEMA_VERSION`` + the two pinned
sandbox image refs via ``nethackers.diagnostics.version_info``, never
Docker or the hub). Its output is data, not human chrome, so it goes to
stdout like every other data render and honors ``-o json`` the same way.

``doctor`` (spec 5.6) answers "is my machine set up to do X?" per
**capability** (``eval``/``evolve``/``publish``/``browse``): it runs
``nethackers.diagnostics.run_checks`` (which never raises -- every probe is
wrapped) and prints either the grouped human summary or ``to_json``'s
``{checks, capabilities, env}`` via the same ``emit`` every read subcommand
uses, then exits via ``exit_code`` -- 0 iff ``--for``'s capability (default
``eval``) is ready; soft warnings never flip it. ``--pull`` is the one
mutating flag (acquires both sandbox images, streaming progress to
``err``, then re-checks). The hub/login checks reuse ``whoami``'s own
``HubClient(...).hub_mode()``/``_load_creds`` primitives -- one source of
truth (INV5), not a second implementation.

``setup`` gets a machine ready in one command: it runs doctor's checks,
builds a plan from the OS recipes (``nethackers.setup``), asks once, runs what
it can -- never ``sudo`` -- and prints the rest. The orchestration lives in
``setup/flow.py`` behind ``SetupDeps``; this module only builds the real
dependencies (``_setup``) and shares ``_do_login`` with ``login``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.text import Text
from rich_argparse import RichHelpFormatter

from nethackers import browser, clipboard, config, crashfile
from nethackers.config import Stage, load_stage
from nethackers.containers import container_runtime, probe_container_runtime
from nethackers.diagnostics import (
    CAPABILITIES,
    _short_digest,
    exit_code,
    render_human,
    render_plain,
    run_checks,
    to_json,
    version_info,
)
from nethackers.eval.runner import eval_batch
from nethackers.harness.discovery import ModelInfo, list_models, preflight_model
from nethackers.harness.launch import EvolveParams, _now, prepare_evolve
from nethackers.harness.pull_events import PullEvent, render_cli_line
from nethackers.harness.sandbox_preflight import (
    download_size,
    ensure_image,
    image_present,
    preflight as sandbox_preflight,
    preflight_operator,
    preflight_runtime,
    resolve_image,
    sandbox_platform_mismatch,
)
from nethackers.hub.objectives import CATALOG
from nethackers.hub.selector import resolve
from nethackers.hub.views.boards import resolve_scope
from nethackers.hubclient import credentials as _cred
from nethackers.hubclient.auth import AuthError, TokenSource
from nethackers.hubclient.client import (
    HubClient,
    HubUnreachable,
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
from nethackers.hubclient.publish import PublishError, ensure_repo, gh_state, publish_solution
from nethackers.hubclient.pull import pull
from nethackers.hubclient.register import GitHubUnreachable, device_login, refresh_access_token
from nethackers.hubclient.render import (
    render_board as rich_board,
    render_elites as rich_elites,
    render_frontier_grid,
    render_search as rich_search,
    render_show as rich_show,
)
from nethackers.operators import DEFAULT_OPERATOR, OPERATORS
from nethackers.setup import flow as setup_flow, render as setup_render, runner as setup_runner
from nethackers.setup.host import detect_host, read_text
from nethackers.solution_root import SolutionRootError
from nethackers.tui.app import NetHackersApp

# Time seam: tests monkeypatch ``cli._time_now`` to make credential
# expiry/refresh deterministic (avoids a wall-clock ``time.time()`` read).
_time_now = time.time


def _login_prompt(verification_uri: str, user_code: str) -> None:
    """Styled device-flow prompt, the ``gh auth login`` way: the one-time code
    in a bordered panel (copied to the clipboard, best effort, so it can be
    pasted, not retyped), then "Press Enter to open <url> in your browser" --
    and on Enter, the browser. All on stderr so ``-o json`` / pipes stay clean.
    ``device_login`` starts polling once this returns, as gh does.

    No clickable (OSC 8) link: only some terminals honor one. The URL is plain
    text, and it is all there is when Enter could open nothing -- stdin or
    stderr isn't a terminal (agents, pipes) or there is no browser here
    (``browser.can_open``: plain SSH to a server) -- so then this doesn't wait."""
    copied = clipboard.copy(user_code)
    body = Text()
    body.append("Your one-time code", style="dim")
    if copied:
        body.append("   (copied to clipboard)", style="green")
    body.append("\n     ")
    body.append(user_code, style="bold yellow")
    err.print(Panel(body, title="[b green]Authorize NetHackers[/]",
                    border_style="green", expand=False, padding=(1, 2)))
    url = Text(verification_uri, style="bold cyan")
    interactive = sys.stdin is not None and sys.stdin.isatty() and err.is_terminal
    if not (interactive and browser.can_open()):
        err.print(Text.assemble("Open ", url, " in a browser and enter the code."))
        return
    err.input(Text.assemble("Press ", ("Enter", "bold"), " to open ", url,
                            " in your browser… "), stream=sys.stdin)
    if not browser.open_url(verification_uri):
        err.print(Text.assemble(("Couldn't open a browser", "yellow"), " — go to ", url,
                                " and enter the code."))


def _load_creds() -> Credentials | None:
    return _cred.load()


def _authed_token() -> str | None:
    """Return a usable access token from the stored credential, refreshing it
    silently when it has expired and a refresh token is on hand.

    Returns ``None`` when there is no stored credential at all (the caller
    prints the "run ``nethackers login``" hint). Otherwise delegates to
    ``TokenSource.current()`` -- the same adapter ``harness.launch.
    prepare_evolve`` wires into an evolve run's ``HubClient``, so there is
    exactly one implementation of "refresh before it's needed" in this
    codebase. Raises ``AuthError`` (caught by ``main``'s top-level guard,
    rendered as a plain hint) when the credential is expired and the
    refresh itself fails -- there is no usable token left to fall back to."""
    creds = _cred.load()
    if creds is None:
        return None
    return TokenSource(creds, refresh=refresh_access_token, now=_time_now).current()


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


def _report_summary(crash: dict[str, Any]) -> str:
    """Human-readable rendering of one crash file for ``nethackers
    report``'s default/table/plain output. Deliberately plain text, no rich
    markup: this is meant to be read once, then copied verbatim into a
    GitHub issue or a chat message -- not decorated. ``-o json`` bypasses
    this entirely (``emit`` falls back to a raw ``json.dumps`` of the crash
    dict itself, per the zero-telemetry "only DISPLAYS the local file, never
    reshapes it for a person" contract)."""
    doctor = crash.get("doctor")
    caps = doctor.get("capabilities") if isinstance(doctor, dict) else None
    lines = [
        f"crash report — {crash.get('ts', 'unknown time')}",
        f"  nethackers  {crash.get('nethackers_version', '?')}",
        f"  python      {crash.get('python', '?')}",
        f"  platform    {crash.get('platform', '?')}",
        f"  command     {' '.join(crash.get('argv', []))}",
        f"  error       {crash.get('exc_type', '?')}",
    ]
    if caps:
        ready = ", ".join(f"{name}={'yes' if ok else 'no'}" for name, ok in caps.items())
        lines.append(f"  ready to    {ready}")
    else:
        lines.append("  doctor      not available")
    return "\n".join(lines)


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
    ``stage.hub_url``/``"auto"`` on ``_build_parser``'s own flags) survives
    untouched otherwise -- giving exactly "subcommand-level wins if given,
    else the top-level value" without hand-rolling the merge.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--hub",
        default=argparse.SUPPRESS,
        help="Hub API base URL (default: the top-level --hub, itself "
        "$NETHACKERS_HUB or the active stage).",
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


def _stage_from_argv(argv: list[str] | None) -> Stage:
    """Pre-scan argv for ``--prod`` before ``_build_parser`` exists -- that
    parser needs a resolved ``Stage`` as input (for ``--hub``/
    ``--mutator-image``/etc.'s own defaults), so this one flag has to be
    read before argparse can run at all. ``--prod`` forces the prod stage
    by disabling ``.env.stack`` discovery outright -- ``NETHACKERS_STAGE_
    FILE=""``, the same explicit-empty hatch ``load_stage``'s file layer
    already understands -- while real process env (and true CLI flags,
    applied afterward by argparse) still win over it, same as any other
    invocation."""
    scan = sys.argv[1:] if argv is None else argv
    if "--prod" in scan:
        return load_stage(environ={**os.environ, "NETHACKERS_STAGE_FILE": ""})
    return load_stage()


# The offline-hub mismatch hint, shared verbatim between `_where_line`'s rich
# line and `_effective_identity`'s plain (JSON) string -- a locally-logged-in
# identity that a pointed-at offline/stub hub will never accept.
_OFFLINE_MISMATCH_HINT = (
    "OFFLINE hub — your login isn't accepted here (run the hub with HUB_AUTH=github)"
)


def _where_line(stage: Stage, login: str | None, hub_mode: str | None, *,
                unreachable: bool = False) -> str:
    """The ``whoami`` "where am I pointed" line: EFFECTIVE identity -- who
    you are TO THE HUB you're pointed at, not just your local login state --
    plus stage + hub. Unlike the ambient indicators (the ``_run`` startup dim
    line, the TUI idbar), which stay silent for ``prod`` to avoid noise on
    every single invocation, this always names the stage -- a user who
    explicitly asked "whoami" wants the full picture, prod included.

    ``hub_mode`` is the hub's reported ``/healthz`` ``auth`` field
    (``"offline"``/``"github"``, or ``None`` when unknown -- an old hub
    predating this feature, or a mode a future hub reports that this client
    doesn't recognize -- treated the same as ``"github"``: no annotation,
    today's plain login/guest line, since there's no positive evidence the
    hub can't accept a real login). ``unreachable`` -- the hub couldn't be
    reached at all -- takes precedence over everything else: there's no
    identity to assert against a hub that isn't even there. This is the
    motivating bug's fix: a real login pointed at a local offline/stub hub
    used to get a bare 401 on register with no hint why, and whoami showed
    the login as if it would work there."""
    host = stage.hub_url.split("//")[-1]
    if unreachable:
        return f"[red]hub unreachable at {stage.hub_url}[/] · stage:{stage.name}"
    if hub_mode == "offline":
        if login is None:
            return f"[b]OFFLINE[/] · stage:{stage.name} · hub:{host}"
        return (f"[b]@{login}[/]  [yellow]⚠ {_OFFLINE_MISMATCH_HINT}[/] · "
                f"stage:{stage.name} · hub:{host}")
    if login is not None:
        return f"[b]@{login}[/] · stage:{stage.name} · hub:{host}"
    return (
        f"[yellow]not logged in (guest)[/] · stage:{stage.name} · hub:{host} "
        "— browse/offline only; `login` to publish"
    )


def _effective_identity(login: str | None, hub_mode: str | None, *,
                        unreachable: bool = False) -> str:
    """Plain (no rich markup) counterpart of ``_where_line``'s identity
    segment, for ``whoami --output json``'s ``"effective"`` field. Never
    includes a token -- only the login/mode/reachability are ever plumbed in
    here."""
    if unreachable:
        return "hub unreachable"
    if hub_mode == "offline":
        return "OFFLINE" if login is None else f"@{login} ({_OFFLINE_MISMATCH_HINT})"
    return f"@{login}" if login is not None else "not logged in"


def _build_parser(stage: Stage) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nethackers",
        formatter_class=RichHelpFormatter,
        description=(
            "NetHackers — a distributed effort to 'solve' NetHack by evolving "
            "deterministic symbolic players (built on AutoAscend), coordinated through "
            "a shared hub. This CLI browses the hub — the attainment map, ranking "
            "boards, and elite programs — and lets you evaluate and register your own. "
            "Reads print a rich table in a terminal and JSON when piped; use -o to "
            "force a format."
        ),
        epilog=(
            "Env: NETHACKERS_HUB sets the hub URL; NETHACKERS_OUTPUT sets the default "
            "-o/--output format."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version, run-schema version, and pinned "
        "sandbox image digests, then exit (offline; honors -o json). Not "
        "argparse's built-in version action -- that can't honor -o.",
    )
    parser.add_argument(
        "--hub",
        default=stage.hub_url,
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
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Force the prod stage, ignoring any .env.stack (e.g. hitting the "
        "global hub from a worktree).",
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

    do = sub.add_parser(
        "doctor", parents=[common], formatter_class=RichHelpFormatter,
        help="Check whether this machine is set up to eval/evolve/publish/browse.",
    )
    do.add_argument(
        "--for", dest="for_capability", choices=list(CAPABILITIES), default=None,
        help="Check readiness for one capability only, and exit accordingly "
        "(default: eval -- the minimum useful).",
    )
    do.add_argument(
        "--pull", action="store_true",
        help="Pull the arena+mutator sandbox images first (progress on stderr), then "
        "re-check. `nethackers setup` does this and the rest of setting up.",
    )
    do.add_argument(
        "--operator", choices=list(OPERATORS), default=None,
        help="Restrict the operator-readiness check to one agent "
        "(default: all registered coding agents).",
    )

    su = sub.add_parser(
        "setup", parents=[common], formatter_class=RichHelpFormatter,
        help="Get this machine ready: container runtime, sandbox images, logins, coding "
        "agent. Shows its plan and asks once; never runs sudo.",
    )
    su.add_argument(
        "--for", dest="for_capability", choices=list(CAPABILITIES), default=None,
        help="Set up only what one capability needs (default: everything).",
    )
    su.add_argument(
        "--operator", choices=list(OPERATORS), default=None,
        help="The coding agent evolve should use (default: one already logged in, "
        "or ask).",
    )
    su.add_argument(
        "-y", "--yes", action="store_true",
        help="Run the plan without asking. With no terminal, runs the unattended steps "
        "and lists the logins to run.",
    )

    sub.add_parser(
        "report", parents=[common], formatter_class=RichHelpFormatter,
        help="Show the most recent local crash report (read-only and offline -- "
        "nothing is ever sent anywhere; share it yourself if you choose to).",
    )

    e = sub.add_parser(
        "eval", parents=[common], formatter_class=RichHelpFormatter,
        help="Evaluate a solution against a published objective's batch.",
    )
    e.add_argument("solution", help="Path to the solution directory (mounted read-only).")
    e.add_argument("--objective", required=True, help="A catalog objective name.")
    e.add_argument("--image", default=stage.arena_image, help="Arena image to run.")
    e.add_argument(
        "--max-parallel-evals", type=int, default=None,
        help="Cap on episodes the arena runs concurrently (default: one per CPU the "
             "container runtime has, bounded by its memory and the batch).",
    )

    mo = sub.add_parser(
        "models", parents=[common], formatter_class=RichHelpFormatter,
        help="List the models the operator CLI can serve inside the mutator sandbox image.",
    )
    mo.add_argument("--operator", choices=list(OPERATORS), default="codex")
    mo.add_argument(
        "--mutator-image", default=stage.mutator_image,
        help="Probe this image's operator CLI (the one a run uses), not the host's "
        "(default: %(default)s).",
    )

    evolve = sub.add_parser(
        "evolve", parents=[common], formatter_class=RichHelpFormatter,
        help="Evolve a bot for an objective with a headless coding agent.",
    )
    evolve.add_argument("objective")
    evolve.add_argument("--seed", required=True, help="seed solution root (e.g. roots/autoascend)")
    evolve.add_argument(
        "--from-seed", action="store_true",
        help="Ignore the hub; cold-start every cell from --seed.",
    )
    evolve.add_argument(
        "--offline", action="store_true",
        help="Evaluate locally without publishing or registering (still reads "
        "the configured hub for cell-seeding).",
    )
    evolve.add_argument("--operator", choices=list(OPERATORS), default=DEFAULT_OPERATOR)
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
    evolve.add_argument(
        "--mutator-image", default=stage.mutator_image,
        help="Container image the mutator runs in (default: %(default)s). The "
        "mutator always runs sandboxed in this image; a working container "
        "runtime and the selected --operator's host login are required.",
    )
    evolve.add_argument(
        "--iterations", type=int, default=1,
        help="Improvement rounds to run: each picks a random cell and mutates "
             "its elite (default: %(default)s).")
    evolve.add_argument(
        "--max-parallel-evals", type=int, default=None,
        help="Cap on episodes the arena runs concurrently per eval (default: one per "
             "CPU the container runtime has, bounded by its memory and the batch).",
    )
    evolve.add_argument("--image", default=stage.arena_image)
    evolve.add_argument(
        "--token", default=None,
        help="Attribution token (default: stored `nethackers login` credentials, "
        "else 'offline-token').",
    )
    evolve.add_argument(
        "--owner", default=None,
        help="Attribution owner (default: stored `nethackers login` credentials, "
        "else 'offline').",
    )
    evolve.add_argument("--workdir", default=str(stage.data_root))
    evolve.add_argument(
        "--run-name", default=None,
        help="Optional label appended to the run-id folder under runs/.",
    )
    evolve.add_argument(
        "--verified", action="store_true",
        help="Seed only from the VERIFIED network (trusted scores on hidden seeds); "
        "default is the fast self-reported network. Either way, every pulled "
        "program runs in the sealed sandbox.",
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
        "Pass a program id to pick a specific one.",
    )

    el = sub.add_parser(
        "elites", parents=[common], formatter_class=RichHelpFormatter,
        help="Show the elites (best programs per identity) for a scope.",
    )
    el.add_argument(
        "--scope", default="generalist",
        help="'generalist', a role (e.g. 'val'), a facet (e.g. 'race:elf'), "
        "or a full identity (default: %(default)s).",
    )

    b = sub.add_parser(
        "leaderboard", aliases=["board"], parents=[common],
        formatter_class=RichHelpFormatter,
        help="Show the leaderboard — programs ranked by scope.",
    )
    b.add_argument(
        "--scope", default="generalist",
        help="'generalist', a role (e.g. 'val'), a facet (e.g. 'race:elf'), "
        "or a full identity (default: %(default)s).",
    )

    se = sub.add_parser(
        "search", parents=[common], formatter_class=RichHelpFormatter,
        help="Search registered programs.",
    )
    se.add_argument("--owner", default=None, help="Narrow to one owner login.")
    se.add_argument("--limit", type=int, default=50)
    se.add_argument("--offset", type=int, default=0)

    sh = sub.add_parser(
        "show", parents=[common], formatter_class=RichHelpFormatter,
        help="Show one registered program by id.",
    )
    sh.add_argument("id")

    r = sub.add_parser(
        "register", parents=[common], formatter_class=RichHelpFormatter,
        help="Register a repo@commit program link with the hub (uses your stored login).",
    )
    r.add_argument("--repo", required=True, help="e.g. github.com/owner/name")
    r.add_argument("--commit", required=True, help="40-hex commit sha")
    r.add_argument("--evidence", required=True,
                   help="Path to an evidence JSON (from `nethackers eval`).")
    r.add_argument("--root", default=".",
                   help="Solution root within the repo (default: the repo root).")
    r.add_argument("--entrypoint", default="bot.py",
                   help="Solution entrypoint file (default: %(default)s).")

    sm = sub.add_parser(
        "submit", parents=[common], formatter_class=RichHelpFormatter,
        help="Publish a solution to your public <you>/nethacker repo (via gh) and register it.",
    )
    sm.add_argument("solution_dir", help="Path to the solution directory to publish.")
    sm.add_argument(
        "--repo-name", default=stage.repo_name,
        help="Repo under your account to publish into (default: %(default)s).",
    )
    sm.add_argument(
        "--message", default="nethackers submit",
        help="Commit message for the published solution.",
    )
    sm.add_argument("--objective", required=True,
                    help="A catalog objective name to evaluate on (self-reported score).")
    sm.add_argument("--image", default=stage.arena_image, help="Arena image to run.")
    sm.add_argument("--max-parallel-evals", type=int, default=None,
                    help="Cap on concurrent episodes (default: one per CPU the container "
                         "runtime has, bounded by its memory and the batch).")

    return parser


def _unknown_objective(name: str) -> str:
    return (
        f"unknown objective {name!r}. Use a full identity such as "
        f"'wiz-elf-cha-mal', a role (e.g. 'wiz'), a comma list, or a glob like "
        f"'*-elf-*-*' (the hub catalog has {len(CATALOG)} objectives)."
    )


def _unknown_scope(name: str) -> str:
    return (
        f"unknown scope {name!r}. Use 'generalist', a role (e.g. 'val'), "
        f"a facet (e.g. 'race:elf'), or a full identity (e.g. 'wiz-elf-cha-mal')."
    )


def _short_pin(ref: str) -> str:
    """Format one pinned ``<repo>@sha256:<64-hex>`` image ref for
    ``--version``'s human block (spec 5.7): the registry/repo path is long
    and identical shape across both pins, and the full 64-hex digest is
    illegible on a terminal line, so both are elided behind a leading/
    trailing ellipsis, keeping only a 19-char digest prefix -- e.g.
    ``…@sha256:0000000000000000000…``. ``-o json`` (``version_info()``)
    always carries the untruncated ref -- this truncation is display-only.

    Delegates the actual digest-shortening to ``diagnostics._short_digest``
    (``doctor``'s own display-layer truncation, spec 5.6) so the two share
    one source of truth for the prefix length -- only the leading ellipsis
    (eliding the repo path too, fine here but wrong for ``doctor``, where
    the repo path is useful context mid-sentence) is added on top."""
    return "…" + _short_digest(ref[ref.index("@sha256:"):])


def _arena_preflight(image: str, *, runtime: str | None) -> str | None:
    """The arena-only gate ``eval``/``submit`` share: a working container
    runtime, then the (already-resolved) image itself, built/pulled if
    missing. ``None`` on success, else the first failing check's styled
    message. Deliberately calls ONLY ``preflight_runtime`` -- never
    ``preflight_operator`` -- the arena has no operator, so a plain
    eval/submit must never demand a codex/claude login (spec S5.5's "two
    separate gates").

    ``runtime`` is the resolved container CLI (``container_runtime()``, docker
    or podman -- issue #50), passed once by the caller and threaded into
    ``ensure_image`` so the pull uses the same binary the gate accepted.
    ``preflight_runtime`` stays the gate (and the source of the styled "no
    runtime" message), so a ``None`` runtime is caught there, not here.

    Shows the same CLI progress display as ``setup``/``doctor --pull``
    (``_pull_progress``) rather than a raw docker dump -- ``eval``'s and
    ``submit``'s first pull gets megabytes, speed, and time left too."""
    rt_err = preflight_runtime(scope="eval")
    if rt_err is not None:
        return rt_err
    with _pull_progress() as on_event:
        return ensure_image(image, "arena", runtime=runtime or "docker", on_event=on_event)


@contextmanager
def _pull_progress(total: int | None = None) -> Iterator[Callable[[PullEvent], None]]:
    """A ``PullEvent`` consumer for CLI sandbox provisioning (spec S5.5; the
    TUI has its own). On a terminal: one bar across every image pulled inside
    this block -- megabytes, speed, and time left once rich has measured a
    rate -- with ``total`` (bytes, from the registry) as the length when known.
    Before any byte count arrives (Podman, or a pipe) the description shows the
    layer count. Redirected output gets plain lines instead, one per layer
    change -- never one per byte update.

    The registry total counts every layer, so it overclaims after a re-pin,
    when most layers are already here: once docker says a layer "Already
    exists" in the first image of the block, the bar's length becomes what
    docker reports it is downloading. A later image's "Already exists" is a
    layer shared with the image just pulled, which the total counted once."""
    if not err.is_terminal:
        def _on_event_plain(event: PullEvent) -> None:
            if event.phase == "layer" and event.detail.startswith(("Downloading", "Extracting")):
                return
            err.print(f"[dim]{render_cli_line(event)}[/]")
        yield _on_event_plain
        return
    columns = (SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
               DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn())
    with Progress(*columns, console=err, transient=True) as progress:
        task = progress.add_task("pulling", total=total)
        finished = 0   # bytes of images already pulled in this block
        current = 0
        images_done = 0
        upgrade = False   # layers were already here: the registry total overclaims

        def _on_event_bar(event: PullEvent) -> None:
            nonlocal finished, current, images_done, upgrade
            if event.detail == "Already exists" and images_done == 0:
                upgrade = True
            if event.bytes_done is not None:
                current = event.bytes_done
                known = finished + (event.bytes_total or 0)
                progress.update(task, description=f"pulling {event.kind}",
                                completed=finished + current,
                                total=(known if upgrade else max(total or 0, known)) or None)
            else:
                progress.update(task, description=render_cli_line(event))
            if event.phase in ("done", "error"):
                finished += current
                current = 0
                images_done += 1

        yield _on_event_bar


def _do_login(stage: Stage) -> str:
    """The GitHub device flow, stored as the hub credential; returns the login.
    Shared by `login` and `setup`, so there is one copy of it."""
    tok = device_login(prompt=_login_prompt, client_id=stage.github_client_id)
    login = whoami_from_token(tok["access_token"])
    _cred.save(Credentials(
        login=login,
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        expires_at=(_time_now() + tok["expires_in"]) if tok["expires_in"] else None,
    ))
    return login


def _setup_pull(kinds: tuple[str, ...], total: int | None) -> str | None:
    """Pull the named sandbox images with the CLI's progress display; the first
    error, or ``None``. ``total`` (bytes) is used by the progress bar."""
    runtime = container_runtime()
    if runtime is None:
        return "no usable container runtime"
    with _pull_progress(total) as on_event:
        for kind in kinds:
            perr = ensure_image(resolve_image(None, kind), kind, runtime=runtime,
                                on_event=on_event)
            if perr is not None:
                return perr
    return None


def _setup_pull_size(kinds: tuple[str, ...]) -> int | None:
    """Bytes a pull of ``kinds`` will download, from the registry: layers
    shared by both images counted once, minus the layers of an image already
    here. ``None`` when it can't be read (no runtime yet, offline, Podman)."""
    runtime = container_runtime()
    if runtime is None:
        return None
    present = []
    for k in ("arena", "mutator"):
        if k in kinds:
            continue
        ref = resolve_image(None, k)
        if image_present(ref, runtime=runtime):
            present.append(ref)
    return download_size([resolve_image(None, k) for k in kinds], present, runtime=runtime)


def _setup_confirm() -> bool:
    """setup's one question. End of input (Ctrl-D, or a script's empty stdin)
    is a no: nothing runs without an explicit yes."""
    try:
        return Confirm.ask("Continue?", default=True, console=err)
    except EOFError:
        err.print()  # end the prompt's line
        return False


def _setup_ask_agent() -> str:
    """Which coding agent evolve should use. End of input aborts setup the way
    Ctrl-C does (``main`` prints "aborted", exit 130), rather than taking the
    default as an answer."""
    try:
        return Prompt.ask("Which coding agent will evolve use?", choices=list(OPERATORS),
                          default=DEFAULT_OPERATOR, console=err)
    except EOFError:
        err.print()
        raise KeyboardInterrupt from None


def _setup(args: argparse.Namespace, stage: Stage) -> int:
    interactive = sys.stdin is not None and sys.stdin.isatty() and err.is_terminal
    opts = setup_flow.SetupOptions(scope=args.for_capability, operator=args.operator,
                                   yes=args.yes, interactive=interactive, hub=args.hub)

    def report(checks, summary) -> None:
        emit(to_json(checks), args.output,
             table=lambda _d: setup_render.summary_rich(summary),
             plain=lambda _d: setup_render.summary_plain(summary))

    deps = setup_flow.SetupDeps(
        console=err,
        run_checks=run_checks,
        detect_host=detect_host,
        probe_runtime=probe_container_runtime,
        gh_state=gh_state,
        load_creds=_load_creds,
        agent_logged_in=lambda op: preflight_operator(op) is None,
        resolve_exe=lambda name: setup_flow.resolve_exe(name, which=shutil.which,
                                                         home=Path.home()),
        which=shutil.which,
        read_text=read_text,
        hub_login=lambda: _do_login(stage),
        pull=_setup_pull,
        pull_size=_setup_pull_size,
        run_terminal=setup_runner.run_terminal,
        run_captured=lambda argv, title: setup_runner.run_captured(argv, title=title,
                                                                   console=err),
        ask_agent=_setup_ask_agent,
        confirm=_setup_confirm,
        report=report,
    )
    return setup_flow.run_setup(opts, deps)


def _run(argv: list[str] | None) -> int:
    """Parse args and dispatch one subcommand. May raise -- ``main`` is the
    single place that turns any failure into a clean message, so nothing here
    needs its own try/except for hub I/O."""
    stage = _stage_from_argv(argv)
    parser = _build_parser(stage)
    args = parser.parse_args(argv)

    if args.version:
        # Ahead of the stage announcement and every subcommand/TUI path --
        # `--version` is a self-contained, offline diagnostic (spec 5.7/
        # INV7: reports the pins this build was cut against, never touches
        # Docker/network/hub) and must stay that way regardless of --hub or
        # stage. `-o json` gets the untruncated refs on stdout; the human
        # block shortens the two image digests for readability (see
        # `_short_pin`) -- either way, nothing but this data hits stdout.
        info = version_info()
        if args.output == "json":
            print(json.dumps(info))
        else:
            print(f"nethackers {info['nethackers']}")
            print(f"run-schema {info['run_schema_version']}")
            print(f"arena {_short_pin(info['images']['arena'])}")
            print(f"mutator {_short_pin(info['images']['mutator'])}")
        return 0

    if stage.name != "prod":
        # An ambient discovery visibility requirement: implicit .env.stack
        # discovery must never be silent -- stderr, so `-o json` stays
        # machine-clean; the *effective* hub (after any --hub flag), not
        # just the stage's own default, since that's the one that actually
        # matters for what this invocation is about to touch.
        err.print(f"[dim]stage: {stage.name} · hub {args.hub}[/]")

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
        login = _do_login(stage)
        err.print(f"logged in as [b]@{login}[/]")
        return 0

    if args.cmd == "logout":
        _cred.clear()
        err.print("logged out")
        return 0

    if args.cmd == "whoami":
        c = _load_creds()
        ident = c.login if c is not None else None
        # Effective identity is who you are TO THE HUB you're pointed at, not
        # just your local login state -- fetch its reported auth mode so a
        # mismatch (a real login against a local offline/stub hub) is
        # surfaced here instead of as a bare 401 on register. Unreachable is
        # handled locally (never a raw ConnectionError / traceback).
        hub_mode: str | None = None
        unreachable = False
        try:
            hub_mode = HubClient(args.hub).hub_mode()
        except HubUnreachable:
            unreachable = True
        if args.output == "json":
            print(json.dumps({
                "login": ident,
                "authenticated": c is not None,
                "stage": stage.name,
                "hub": stage.hub_url,
                "hub_auth": hub_mode,
                "effective": _effective_identity(ident, hub_mode, unreachable=unreachable),
            }))
        else:
            err.print(_where_line(stage, ident, hub_mode, unreachable=unreachable))
        return 0 if c is not None else 1

    if args.cmd == "doctor":
        if args.pull:
            # Acquire both sandbox images unconditionally -- ensure_image
            # itself no-ops when a ref is already present, so this is cheap
            # on a machine that's already set up. Never bails early on one
            # failure: attempt both, stream each to stderr, then re-check
            # regardless -- the checks below give an accurate post-attempt
            # picture either way (this is doctor's one mutating path; every
            # other branch here is read-only). Resolve the runtime (docker or
            # podman -- issue #50) ONCE and thread it in; with none usable there
            # is nothing to pull WITH, so skip and let the container_runtime
            # check below explain the real cause rather than mislabel a missing
            # binary as "couldn't reach the registry".
            pull_runtime = container_runtime()
            if pull_runtime is None:
                err.print("[yellow]skipping --pull[/]: no usable container runtime "
                          "(see the container_runtime check below)")
            else:
                for kind in ("arena", "mutator"):
                    ref = resolve_image(None, kind)
                    err.print(f"[dim]checking/pulling {kind} sandbox ({ref})…[/]")
                    with _pull_progress() as on_event:
                        perr = ensure_image(ref, kind, runtime=pull_runtime, on_event=on_event)
                    if perr is not None:
                        err.print(perr)
        # Named distinctly from `evolve`'s own `results` local below -- both
        # live in this same un-annotated function scope (Python has no
        # per-`if`-block scoping), and mypy widens a bare local's inferred
        # type across every assignment to that name in the whole function.
        checks = run_checks(operator=args.operator, hub=args.hub)
        emit(
            to_json(checks), args.output,
            table=lambda _d: render_human(checks),
            plain=lambda _d: render_plain(checks),
        )
        return exit_code(checks, args.for_capability)

    if args.cmd == "setup":
        return _setup(args, stage)

    if args.cmd == "report":
        # Read-only and offline, unlike every other branch above/below that
        # touches the hub: this only ever displays a file `write_crash`
        # already wrote (main()'s top-level exception guard) -- it makes no
        # network call and never triggers a fresh doctor probe itself.
        path = crashfile.latest()
        if path is None:
            err.print("no crash reports found")
            return 0
        crash = crashfile.load(path)
        emit(crash, args.output, table=_report_summary, plain=_report_summary)
        return 0

    if args.cmd == "eval":
        spec = CATALOG.get(args.objective)
        if spec is None:
            err.print(_unknown_objective(args.objective))
            return 2
        image = resolve_image(args.image, "arena")
        runtime = container_runtime()
        pf_err = _arena_preflight(image, runtime=runtime)
        if pf_err is not None:
            err.print(pf_err)
            return 1
        evidence = eval_batch(
            Path(args.solution), spec, image, now=_now(), runtime=runtime or "docker",
            max_parallel_evals=args.max_parallel_evals,
        )
        print(json.dumps(evidence.to_dict(), indent=2))
        return 0

    if args.cmd == "models":
        models: list[ModelInfo] | None = list_models(
            args.operator, image=resolve_image(args.mutator_image, "mutator"),
            docker=container_runtime() or "docker")
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
        # Validate the objective selector before anything docker/sandbox-shaped
        # (sandbox_preflight below can fail first and mask a bad selector, and a
        # doomed run shouldn't wait on a container probe to find out it's doomed).
        # random/all are retired (Task A1) -- resolve() itself rejects them now,
        # the same unknown-objective path as any other unrecognized token.
        try:
            resolve(args.objective)
        except ValueError:
            err.print(_unknown_objective(args.objective))   # "unknown objective 'X'. Use <forms>"
            return 2
        if args.operator == "opencode2" and args.effort and not args.model:
            # OpenCode 2 has no effort flag: effort is a variant of a named
            # model (`provider/model#variant`), so there is nothing to attach it to.
            err.print("[red]opencode2 applies --effort as a model variant[/] — "
                      "pin a model with `--model provider/model`, or drop --effort.")
            return 2

        # The mutator ALWAYS runs sandboxed -- there is no host-execution path.
        # Fail fast, before any hub SELECT call / run-dir creation, rather than a
        # mid-loop crash. The same preflight backs the in-app form (evolve_form).
        msg = sandbox_preflight(args.operator)
        if msg is not None:
            err.print(msg)
            return 1
        # Resolve the container runtime ONCE (docker/podman -- issue #50); the
        # preflight above already confirmed one is usable, so this is non-None.
        # It threads into every `<runtime> …` below (auto-provision) AND onto
        # EvolveParams, so the whole run (arena evals + the mutator container)
        # uses the same detected binary.
        evolve_runtime = container_runtime() or "docker"
        # One resolution covers every mutator-image use below (auto-provision,
        # EvolveParams, model preflight) -- never re-read args.mutator_image
        # directly past this point. Same for the arena image: run_loop scores
        # every iteration through it (harness/evaluate.py -> eval_batch), so it
        # needs provisioning up front exactly like the mutator does -- an
        # unset-up arena image would otherwise fail deep inside the first
        # iteration instead of here, at Start.
        mut = resolve_image(args.mutator_image, "mutator")
        arena_img = resolve_image(args.image, "arena")
        # Auto-provision both sandbox images (users never run `make`/`docker
        # pull` themselves): whichever isn't present yet is acquired here with
        # a one-time progress note.
        for _ref, _kind in ((mut, "mutator"), (arena_img, "arena")):
            if image_present(_ref, runtime=evolve_runtime):
                continue
            err.print(f"[yellow]setting up the {_kind} sandbox[/] (first run — this "
                      "can take a few minutes)…")
            with _pull_progress() as on_event:
                ierr = ensure_image(_ref, _kind, runtime=evolve_runtime, on_event=on_event)
            if ierr is not None:
                err.print(ierr)
                return 1
            err.print(f"[green]✓ {_kind} sandbox ready[/]")
        # The agent scores its own candidates inside the mutator: on another
        # platform than the arena it would optimize games the arena never plays.
        msg = sandbox_platform_mismatch(arena_img, mut, runtime=evolve_runtime)
        if msg is not None:
            err.print(msg)
            return 1

        _creds = _load_creds()
        # run.json + run wiring live in prepare_evolve, shared with the in-app
        # form. The MAP-Elites loop seeds its cells from the hub itself, so
        # there's no pre-loop SELECT here anymore.
        params = EvolveParams(
            objective=args.objective, seed=str(args.seed), operator=args.operator,
            iterations=args.iterations,
            max_parallel_evals=args.max_parallel_evals,
            image=arena_img, hub=args.hub, workdir=args.workdir,
            run_name=args.run_name,
            token=args.token or (_creds.access_token if _creds else config.OFFLINE_TOKEN),
            owner=args.owner or (_creds.login if _creds else config.OFFLINE_OWNER),
            from_seed=args.from_seed, offline=args.offline,
            model=args.model, effort=args.effort, mutator_image=mut,
            runtime=evolve_runtime,
            tier="verified" if args.verified else "self-reported",
        )
        # An anonymous run is offline by necessity (the owner==OFFLINE_OWNER
        # backstop in _publisher_for), but --offline is the only case that says
        # so up front -- without this note, a caller who forgot `nethackers
        # login` would only find out several iterations in, as a silent
        # per-iteration "local-only" outcome.
        if not args.offline and params.owner == config.OFFLINE_OWNER:
            err.print(
                "[dim]not logged in — running offline "
                "(publishing needs `nethackers login`)[/]"
            )
        elif not args.offline:
            # Hub-logged-in but gh not ready ⇒ wins evolve, are found, and
            # silently stay LOCAL (PublishError at push time). Warn up front.
            _gh_login, _gh_state = gh_state()
            if _gh_state == "missing":
                err.print("[yellow]wins won't publish[/] — install the GitHub CLI "
                          "(`gh`), then run `gh auth login` — or run "
                          "`nethackers setup --for publish`")
            elif _gh_state == "unauthed":
                err.print("[yellow]wins won't publish[/] — run `gh auth login` "
                          "(separate from `nethackers login`) — or run "
                          "`nethackers setup --for publish`")

        # Preflight only when a model is pinned: harness-default has nothing to
        # validate, and this keeps the model=None path (the common case + every
        # existing wiring test) free of any CLI/network probe. A confident
        # refuse stops here -- no run dir, no doomed spin; unknown only warns.
        if args.model:
            pf = preflight_model(args.operator, args.model, image=mut, docker=evolve_runtime)
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
            pid = args.program or None
            if pid is None:
                champ = champion(client)
                if champ is None:
                    emit(
                        {}, args.output,
                        table=lambda s: Text("no ranked programs yet.", style="dim"),
                        plain=lambda s: "no ranked programs yet.",
                    )
                    return 0
                pid, owner = champ
                note = f"@{owner}/{pid} — this one program across all identities"
            scores = dict(champion_scores(client, pid))
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
        try:
            resolve_scope(args.scope)
        except ValueError:
            err.print(_unknown_scope(args.scope))
            return 2
        client = HubClient(args.hub)
        emit(client.elites(args.scope), args.output, table=rich_elites, plain=plain_elites)
        return 0

    if args.cmd in ("leaderboard", "board"):
        try:
            resolve_scope(args.scope)
        except ValueError:
            err.print(_unknown_scope(args.scope))
            return 2
        client = HubClient(args.hub)
        emit(client.board(scope=args.scope), args.output,
             table=rich_board, plain=plain_board)
        return 0

    if args.cmd == "search":
        client = HubClient(args.hub)
        emit(client.search(args.owner, args.limit, args.offset), args.output,
             table=rich_search, plain=plain_search)
        return 0

    if args.cmd == "show":
        client = HubClient(args.hub)
        emit(client.show(args.id), args.output, table=rich_show, plain=plain_show)
        return 0

    if args.cmd == "register":
        token = _authed_token()
        if token is None:
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        evidence = json.loads(Path(args.evidence).read_text())
        manifest = {"root": args.root, "entrypoint": args.entrypoint,
                    "parents": [], "influences": []}
        result = HubClient(args.hub).register(
            token=token, reference={"repo": args.repo, "commit": args.commit},
            manifest=manifest, evidence=evidence,
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
        gh, gh_st = gh_state()
        if gh is None:
            if gh_st == "missing":
                err.print("[yellow]gh not installed[/] — install the GitHub CLI "
                          "(`gh`), then run `gh auth login` — or run "
                          "`nethackers setup --for publish`")
            else:  # unauthed
                err.print("[yellow]gh not authed[/] — run `gh auth login` "
                          "(separate from `nethackers login`) — or run "
                          "`nethackers setup --for publish`")
            return 1
        if gh != creds.login:
            err.print(f"gh is authed as [b]@{gh}[/] but you're logged in as "
                      f"[b]@{creds.login}[/] — sign in to the same account "
                      "(`nethackers setup --for publish` checks this)")
            return 1
        token = _authed_token()
        if token is None:  # unreachable (creds is set) -- narrows for the type checker
            err.print("[yellow]not logged in[/] — run `nethackers login`")
            return 1
        spec = CATALOG.get(args.objective)
        if spec is None:
            err.print(_unknown_objective(args.objective))
            return 2
        # self-reported score: evaluate the local solution on the objective's
        # batch. Same arena-only gate as `eval` -- no operator/login involved.
        image = resolve_image(args.image, "arena")
        runtime = container_runtime()
        pf_err = _arena_preflight(image, runtime=runtime)
        if pf_err is not None:
            err.print(pf_err)
            return 1
        evidence = eval_batch(
            Path(args.solution_dir), spec, image, now=_now(), runtime=runtime or "docker",
            max_parallel_evals=args.max_parallel_evals,
        )
        slug = f"{creds.login}/{args.repo_name}"
        try:
            ensure_repo(slug)
            # Its own branch, never the default one: publish_solution tree-syncs
            # the branch it pushes to, which would wipe the repo's landing README.
            sha = publish_solution(args.solution_dir, slug, message=args.message,
                                   ref="submit")
        except PublishError as e:
            err.print(f"[red]publish failed[/] — {e}")
            return 1
        mpath = Path(args.solution_dir) / "nethackers.solution.json"
        manifest = (json.loads(mpath.read_text()) if mpath.is_file()
                    else {"root": ".", "entrypoint": "bot.py", "parents": [], "influences": []})
        result = HubClient(args.hub).register(
            token=token, reference={"repo": f"github.com/{slug}", "commit": sha},
            manifest=manifest, evidence=evidence.to_dict(),
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
    # Verify TLS against the OS trust store as well as certifi's bundle, the
    # way gh, git and the browser do. Behind a TLS-inspecting corporate
    # firewall the firewall's CA is only in the OS store, so certifi-only
    # httpx failed every hub call with CERTIFICATE_VERIFY_FAILED. Process-wide
    # on purpose: this is the application entry point, and truststore must
    # never be injected from library code. ImportError = a runtime truststore
    # can't serve; certifi alone still works there.
    try:
        import truststore
    except ImportError:
        pass
    else:
        truststore.inject_into_ssl()
    try:
        return _run(argv)
    except KeyboardInterrupt:
        err.print("[yellow]aborted[/]")
        return 130
    except AuthError as exc:
        # An expired token with no way to refresh -- an expected, actionable
        # condition (same styling as the "not logged in" hints above), never
        # the red "unexpected error" banner a real bug would get.
        err.print(f"[yellow]{exc}[/]")
        return 1
    except GitHubUnreachable as exc:
        # `login` (and token refresh) talk to github.com, NOT the hub -- so a
        # reachability failure there must name GitHub + the network, never the
        # generic "cannot reach the hub / docker compose up -d" below, which
        # sent users chasing a local hub that was never the problem (issue #50).
        err.print(f"[red]can't reach GitHub to sign in[/] ({exc}) — "
                  "check your network connection or DNS")
        return 1
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
    except SolutionRootError as exc:
        # A solution root that can't be scored: absent, a file, or missing its
        # bot.py. That is a usage error -- it must never take the red
        # "unexpected error" path below, which also writes a crash report and
        # tells the user to file it. The message is already a finished
        # sentence (solution_root.check_solution_root).
        err.print(f"[red]{escape(str(exc))}[/]")
        return 2
    except Exception as exc:  # never surface a raw traceback to a user
        if os.environ.get("NETHACKERS_DEBUG"):
            raise
        # Belt-and-suspenders on top of write_crash's own internal guard
        # (crashfile.py): the crash writer must never replace the ORIGINAL
        # exception main() is already handling with a second one of its own.
        # `manifest_reachable=lambda r: False` skips only the slow GHCR probe
        # -- everything else is the full 7-check snapshot `to_json` expects
        # (a filtered `only=` result would report vacuous "ready" for
        # capabilities whose checks never ran); the whole enrich is
        # best-effort regardless (write_crash nulls `doctor` on any failure).
        try:
            path = crashfile.write_crash(
                exc, argv=sys.argv[1:],
                enrich=lambda: to_json(run_checks(manifest_reachable=lambda r: False)),
            )
        except Exception:
            path = None
        err.print(f"[red]nethackers: unexpected error[/]: {type(exc).__name__}: {exc}")
        err.print("[dim](set NETHACKERS_DEBUG=1 for the full traceback)[/]")
        if path is not None:
            err.print("[dim]wrote a crash report — run `nethackers report` to view/share it[/]")
        return 1
