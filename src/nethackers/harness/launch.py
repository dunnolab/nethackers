"""Reusable evolve-run setup shared by the CLI and the in-app launch form:
build the per-run dir + run.json, pick the operator, and return an EvolvePlan
whose `run(callbacks)` drives run_loop with runlog persistence. Owns _now/
_git_sha/_point_latest (moved from cli.py) so neither the CLI nor the TUI has
to, and so importing it never pulls in cli/tui (no import cycle)."""
from __future__ import annotations

import datetime
import random
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nethackers.harness import runlog
from nethackers.harness.container_operator import ContainerOperator
from nethackers.harness.loop import run_loop
from nethackers.harness.select import select_parent
from nethackers.harness.store import LocalTreeStore
from nethackers.harness.version import HARNESS_VERSION
from nethackers.hubclient import credentials as _credentials
from nethackers.hubclient.auth import TokenSource
from nethackers.hubclient.client import HubClient
from nethackers.hubclient.output import err
from nethackers.tui.status import EvolveConfig


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def _point_latest(runs_dir: Path, rid: str) -> None:
    link = runs_dir / "latest"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(rid)
    except OSError as exc:
        err.print(f"[dim]could not update 'latest' symlink: {exc}[/dim]")


def _default_workdir() -> str:
    return str(Path.home() / ".nethackers" / "evolve")


@dataclass
class EvolveParams:
    objective: str
    seed: str  # resolved parent tree (CLI SELECTs it; the TUI form passes a seed root)
    operator: str = "claude"
    iterations: int = 1
    validation_n: int = 15
    islands: int = 1
    reset_period: int | None = None
    max_parallel_evals: int = 8
    image: str = "nethackers/arena:dev"
    hub: str = "http://localhost:8000"
    token: str = "dev-token"
    owner: str = "dev"
    workdir: str = field(default_factory=_default_workdir)
    run_name: str | None = None
    from_seed: bool = False  # skip SELECT; cold-start from `seed` directly
    select_k: int = 1
    select_temp: float = 1.0
    model: str | None = None   # pin the operator's model (None = harness default)
    effort: str | None = None  # reasoning effort level (None = harness default)
    mutator_image: str = "nethackers/mutator:latest"  # image the mutator always runs in


@dataclass
class EvolvePlan:
    cfg: EvolveConfig
    run: Callable[..., list]
    run_dir: Path
    rid: str


def _publisher_for(
    owner: str, run_id: str, repo_name: str = "nethacker"
) -> Callable[[Path], dict[str, str] | None] | None:
    """A ``publish`` hook for ``run_loop``: push a winning worktree to the
    owner's public ``<owner>/<repo_name>`` repo via ``gh`` and return its
    ``{repo, commit}`` -- so the win is fetchable and passes the hub's
    commit-exists check. Pushes go to this run's own branch
    (``evo-harness-<HARNESS_VERSION>/<run_id>``), not the repo's default
    branch, so parallel runs never race on the same fast-forward. Returns
    ``None`` (loop keeps the win as a local elite, unpublished) when
    publishing can't work: no real owner (dev/test), or ``gh`` unavailable /
    not authed (a ``PublishError`` at push time)."""
    if not owner or owner == "dev":
        return None
    from nethackers.hubclient.publish import PublishError, ensure_repo, publish_solution

    slug = f"{owner}/{repo_name}"
    ref = f"evo-harness-{HARNESS_VERSION}/{run_id}"

    def publish(worktree: Path) -> dict[str, str] | None:
        try:
            ensure_repo(slug)
            sha = publish_solution(worktree, slug, message="nethackers evolve win", ref=ref)
        except PublishError:
            return None
        return {"repo": f"github.com/{slug}", "commit": sha}

    return publish


def _authed_hub(base_url: str) -> HubClient:
    """Build the run's HubClient, self-healing across a token refresh when a
    stored login exists (``nethackers login``) -- ``register()`` then
    resolves and retries its own Bearer token via ``TokenSource`` instead of
    being handed a single, possibly-already-expired ``access_token`` (the
    bug: a stale stored token 401s, ``harness.loop`` downgrades that to an
    unpersisted ``report(...)`` line, and the win never reaches the hub even
    though a valid ``refresh_token`` was sitting right there). No stored
    creds (dev/test) -> no source, so ``register()`` falls back to whatever
    ``token=`` it's given -- unchanged pre-adapter behavior. This is the one
    place both the CLI's ``evolve`` and the in-app form's launch path pick
    up the fix, since both funnel through this function."""
    creds = _credentials.load()
    return HubClient(base_url, token_source=TokenSource(creds) if creds is not None else None)


def prepare_evolve(params: EvolveParams, *, git_sha: str | None = None,
                   tree_store: LocalTreeStore | None = None) -> EvolvePlan:
    started = datetime.datetime.now(datetime.UTC)
    runs_dir = Path(params.workdir) / "runs"
    rid = runlog.run_id(started, params.run_name, exists=lambda r: (runs_dir / r).exists())
    run_dir = runs_dir / rid
    # Shared machine-wide content cache (dedup by digest) -- a win registered
    # by one run is instantly a cache hit for the next run's SELECT.
    store = tree_store if tree_store is not None else LocalTreeStore(Path(params.workdir) / "store")
    # ONE client for both this run's SELECT (read-only, never authed) and its
    # registrations (authed, self-refreshing when a login is stored) -- see
    # `_authed_hub`.
    hub = _authed_hub(params.hub)

    # SELECT the parent: the objective's top *trusted* hub elite (so evolution
    # compounds), unless from_seed forces a deliberate cold start. rng is
    # seeded from the run id so a select_k>1 parent choice is reproducible.
    if params.from_seed:
        parent_tree, parent_digest = Path(params.seed), None
    else:
        parent_tree, parent_digest = select_parent(
            hub, params.objective, store, Path(params.seed),
            owner=params.owner, k=params.select_k, temperature=params.select_temp,
            rng=random.Random(rid))

    # `iterations` is PER ISLAND (the intuitive knob): each island gets this
    # many round-robin mutation rounds, so the loop's total round count -- and
    # everything that counts rounds (run_loop's cap, the "iter X/N" progress,
    # the run list) -- is iterations x islands. islands=1 leaves it unchanged.
    total_iterations = params.iterations * params.islands

    runlog.write_run_config(run_dir, {
        "run_id": rid, "created_at": started.isoformat(),
        "harness_version": HARNESS_VERSION,
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "objective": params.objective, "seed": str(params.seed), "operator": params.operator,
        "iterations": total_iterations, "iterations_per_island": params.iterations,
        "validation_n": params.validation_n,
        "max_parallel_evals": params.max_parallel_evals, "image": params.image,
        "mutator_image": params.mutator_image,
        "parent": parent_digest or "seed", "select_k": params.select_k,
        "select_temp": params.select_temp,
        "islands": params.islands, "reset_period": params.reset_period,
        "model": params.model, "effort": params.effort,
    })
    _point_latest(runs_dir, rid)
    # The mutator ALWAYS runs sandboxed: there is no host-execution path. The
    # `operator` is `Any` (run_loop's own param is `Any` too, in loop.py) --
    # duck-typed on `.run(worktree, brief, *, on_line, stop)`.
    operator: Any = ContainerOperator(
        harness=params.operator, image=params.mutator_image,
        model=params.model, effort=params.effort, run_id=rid)
    cfg = EvolveConfig(objective=params.objective, backend=params.operator,
                       iterations=total_iterations, model=params.model,
                       effort=params.effort)

    def run(callbacks: dict, report: Callable[[str], None] = lambda _m: None) -> list:
        def _on_log(tag: str, line: str) -> None:
            runlog.append_log(run_dir, tag, line)
            callbacks["on_log"](tag, line)
        return run_loop(
            objective=params.objective, seed_tree=parent_tree,
            tree_store=store, operator=operator,
            hub=hub, image=params.image, token=params.token,
            owner=params.owner, iterations=total_iterations,
            validation_n=params.validation_n,
            islands=params.islands, reset_period=params.reset_period,
            from_seed=params.from_seed,
            max_parallel_evals=params.max_parallel_evals, stop=callbacks.get("stop"),
            now_fn=_now, report=report, on_episode=callbacks["on_episode"],
            on_state=callbacks["on_state"], on_log=_on_log, workdir=run_dir / "work",
            on_iteration=lambda it, res: runlog.append_metric(
                run_dir, runlog.metric_record(it, res)),
            publish=_publisher_for(params.owner, rid),
        ) or []

    return EvolvePlan(cfg=cfg, run=run, run_dir=run_dir, rid=rid)
