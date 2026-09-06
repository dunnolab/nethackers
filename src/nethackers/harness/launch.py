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

from nethackers import config
from nethackers.config import load_stage
from nethackers.containers import container_runtime
from nethackers.eval.runner import _default_image_digest
from nethackers.harness import runlog
from nethackers.harness.container_operator import ContainerOperator
from nethackers.harness.discovery import detect_cli
from nethackers.harness.loop import run_loop
from nethackers.harness.sandbox_preflight import resolve_image
from nethackers.harness.store import LocalTreeStore
from nethackers.harness.version import RUN_SCHEMA_VERSION
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
    return str(load_stage().data_root)


@dataclass
class EvolveParams:
    objective: str
    seed: str  # cold-start seed root (the MAP-Elites loop seeds cells from the hub itself)
    operator: str = "claude"
    iterations: int = 1
    max_parallel_evals: int = 8
    # The container CLI every `<runtime> run` uses (docker OR podman -- issue
    # #50). Defaults to "docker"; the CLI evolve handler resolves the actual one
    # via `container_runtime()` and sets it here, so the whole run (arena evals
    # AND the mutator container) shells out to the same detected binary.
    runtime: str = "docker"
    # All of the below are late-bound to the active Stage via default_factory
    # -- never read at import time -- so a test's env/monkeypatch (or a future
    # .env.stack) is picked up on every fresh EvolveParams(), not frozen at
    # module load.
    image: str = field(default_factory=lambda: resolve_image(load_stage().arena_image, "arena"))
    hub: str = field(default_factory=lambda: load_stage().hub_url)
    token: str = field(default_factory=lambda: config.OFFLINE_TOKEN)
    owner: str = field(default_factory=lambda: config.OFFLINE_OWNER)
    workdir: str = field(default_factory=_default_workdir)
    run_name: str | None = None
    from_seed: bool = False  # skip SELECT; cold-start from `seed` directly
    offline: bool = False  # explicit no-publish/no-register gate (hub is still read for seeding)
    model: str | None = None   # pin the operator's model (None = harness default)
    effort: str | None = None  # reasoning effort level (None = harness default)
    mutator_image: str = field(
        default_factory=lambda: resolve_image(load_stage().mutator_image, "mutator"))
    repo_name: str = field(default_factory=lambda: load_stage().repo_name)  # <owner>/<repo_name>


@dataclass
class EvolvePlan:
    cfg: EvolveConfig
    run: Callable[..., list]
    run_dir: Path
    rid: str


def _publisher_for(
    owner: str, run_id: str, repo_name: str = "nethacker", *, offline: bool = False
) -> Callable[[Path], dict[str, str] | None] | None:
    """A ``publish`` hook for ``run_loop``: push a winning worktree to the
    owner's public ``<owner>/<repo_name>`` repo via ``gh`` and return its
    ``{repo, commit}`` -- so the win is fetchable and passes the hub's
    commit-exists check. Pushes go to this run's own branch
    (``evo-harness-<RUN_SCHEMA_VERSION>/<run_id>``), not the repo's default
    branch, so parallel runs never race on the same fast-forward. Returns
    ``None`` (loop keeps the win as a local elite, unpublished) when
    publishing can't work: an explicit ``--offline`` (checked first, wins
    regardless of identity), no real owner (offline/test -- the backstop, kept
    for when a caller forgets to gate offline itself), or ``gh`` unavailable
    / not authed (a ``PublishError`` at push time)."""
    if offline:
        return None
    if not owner or owner == config.OFFLINE_OWNER:
        return None
    from nethackers.hubclient.publish import PublishError, ensure_repo, publish_solution

    slug = f"{owner}/{repo_name}"
    ref = f"evo-harness-{RUN_SCHEMA_VERSION}/{run_id}"

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


def _default_operator_version(operator: str, image: str) -> str | None:
    """The in-container ``<operator> --version`` baked into the mutator image
    this run actually uses (not whatever happens to be on the host), via
    ``discovery.detect_cli``. ``detect_cli`` already degrades to
    ``CliInfo(version=None)`` on any probe failure (image absent, docker
    down, unparseable output) -- this thin wrapper just exists so
    ``prepare_evolve`` has an operator-shaped default it can inject a fake
    for in tests."""
    return detect_cli(operator, image=image, docker=container_runtime() or "docker").version


def _best_effort(resolve: Callable[[], str | None]) -> str | None:
    """Provenance is an untrusted debugging breadcrumb (spec INV1/INV7),
    never a gate: a resolver may shell out to docker, which can be absent,
    down, or slow to fail. Collapse any exception to ``None`` rather than
    let a provenance probe crash -- or block -- a run's launch."""
    try:
        return resolve()
    except Exception:
        return None


def prepare_evolve(
    params: EvolveParams, *, git_sha: str | None = None,
    tree_store: LocalTreeStore | None = None,
    image_digest_resolver: Callable[[str], str] | None = None,
    operator_version_resolver: Callable[[str, str], str | None] | None = None,
) -> EvolvePlan:
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

    # No pre-loop SELECT: the MAP-Elites loop seeds every cell from the hub's
    # per-identity elites itself (harness.loop cold start), so launch just
    # hands the cold-start seed straight through. --from-seed additionally
    # tells the loop to ignore the hub for that cell seeding.
    parent_tree = Path(params.seed)

    # Per-run provenance (design 5.9): the resolved *platform* digests of the
    # images this run actually launches, plus the in-container operator
    # version -- distinct from `Evidence.evaluator_image` on an atom (the
    # untrusted per-score trace, INV1/INV7); this is the evolve run's own
    # traceability record. Best-effort: never let a resolver crash the run.
    # The two resolver params default to `None`, not `= _default_...`
    # directly, so the fallback below is looked up by *name* at call time --
    # a bound-at-def-time kwarg default would capture that function object
    # once at import time, making a test's `monkeypatch.setattr(launch,
    # "_default_image_digest", ...)` a silent no-op. An explicitly injected
    # (non-None) resolver always wins over the default.
    img_res = image_digest_resolver or _default_image_digest
    ov_res = operator_version_resolver or _default_operator_version
    arena_image_digest = _best_effort(lambda: img_res(params.image))
    mutator_image_digest = _best_effort(lambda: img_res(params.mutator_image))
    operator_version = _best_effort(
        lambda: ov_res(params.operator, params.mutator_image))

    runlog.write_run_config(run_dir, {
        "run_id": rid, "created_at": started.isoformat(),
        "run_schema_version": RUN_SCHEMA_VERSION,
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "objective": params.objective, "seed": str(params.seed), "operator": params.operator,
        "iterations": params.iterations,
        "max_parallel_evals": params.max_parallel_evals, "image": params.image,
        "mutator_image": params.mutator_image,
        "arena_image_digest": arena_image_digest,
        "mutator_image_digest": mutator_image_digest,
        "operator_version": operator_version,
        "parent": "seed",
        "model": params.model, "effort": params.effort,
    })
    _point_latest(runs_dir, rid)
    # The mutator ALWAYS runs sandboxed: there is no host-execution path. The
    # `operator` is `Any` (run_loop's own param is `Any` too, in loop.py) --
    # duck-typed on `.run(worktree, brief, *, on_line, stop)`.
    operator: Any = ContainerOperator(
        harness=params.operator, image=params.mutator_image,
        model=params.model, effort=params.effort, run_id=rid, docker=params.runtime)
    cfg = EvolveConfig(objective=params.objective, backend=params.operator,
                       iterations=params.iterations, model=params.model,
                       effort=params.effort)

    def run(callbacks: dict, report: Callable[[str], None] = lambda _m: None) -> list:
        def _on_log(tag: str, line: str) -> None:
            runlog.append_log(run_dir, tag, line)
            callbacks["on_log"](tag, line)

        def _on_iteration(it: int, res) -> None:
            runlog.append_metric(run_dir, runlog.metric_record(it, res))
            fwd = callbacks.get("on_iteration")
            if fwd is not None:
                fwd(it, res)
        return run_loop(
            objective=params.objective, seed_tree=parent_tree,
            tree_store=store, operator=operator,
            hub=hub, image=params.image, token=params.token,
            owner=params.owner, iterations=params.iterations,
            from_seed=params.from_seed, rng=random.Random(rid),
            max_parallel_evals=params.max_parallel_evals, stop=callbacks.get("stop"),
            runtime=params.runtime,
            now_fn=_now, report=report, on_episode=callbacks["on_episode"],
            on_state=callbacks["on_state"], on_log=_on_log, workdir=run_dir / "work",
            on_iteration=_on_iteration,
            publish=_publisher_for(params.owner, rid, repo_name=params.repo_name,
                                    offline=params.offline),
        ) or []

    return EvolvePlan(cfg=cfg, run=run, run_dir=run_dir, rid=rid)
