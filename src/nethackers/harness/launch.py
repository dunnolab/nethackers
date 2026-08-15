"""Reusable evolve-run setup shared by the CLI and the in-app launch form:
build the per-run dir + run.json, pick the operator, and return an EvolvePlan
whose `run(callbacks)` drives run_loop with runlog persistence. Owns _now/
_git_sha/_point_latest (moved from cli.py) so neither the CLI nor the TUI has
to, and so importing it never pulls in cli/tui (no import cycle)."""
from __future__ import annotations

import datetime
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nethackers.harness import runlog
from nethackers.harness.loop import run_loop
from nethackers.harness.operator import ClaudeOperator, CodexOperator
from nethackers.harness.store import LocalTreeStore
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
    seed: str
    operator: str = "claude"
    iterations: int = 1
    token_budget: int = 200_000
    timeout: float = 1800.0
    heldout_n: int = 8
    max_parallel_evals: int = 8
    image: str = "nethackers/arena:dev"
    hub: str = "http://localhost:8000"
    token: str = "dev-token"
    owner: str = "dev"
    workdir: str = field(default_factory=_default_workdir)
    run_name: str | None = None


@dataclass
class EvolvePlan:
    cfg: EvolveConfig
    run: Callable[..., list]
    run_dir: Path
    rid: str


def prepare_evolve(params: EvolveParams, *, git_sha: str | None = None) -> EvolvePlan:
    started = datetime.datetime.now(datetime.UTC)
    runs_dir = Path(params.workdir) / "runs"
    rid = runlog.run_id(started, params.run_name, exists=lambda r: (runs_dir / r).exists())
    run_dir = runs_dir / rid
    runlog.write_run_config(run_dir, {
        "run_id": rid, "created_at": started.isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "objective": params.objective, "seed": str(params.seed), "operator": params.operator,
        "iterations": params.iterations, "token_budget": params.token_budget,
        "timeout": params.timeout, "heldout_n": params.heldout_n,
        "max_parallel_evals": params.max_parallel_evals, "image": params.image,
    })
    _point_latest(runs_dir, rid)
    operator = {"codex": CodexOperator, "claude": ClaudeOperator}[params.operator]()
    cfg = EvolveConfig(objective=params.objective, backend=params.operator,
                       iterations=params.iterations, token_budget=params.token_budget)

    def run(callbacks: dict, report: Callable[[str], None] = lambda _m: None) -> list:
        def _on_log(tag: str, line: str) -> None:
            runlog.append_log(run_dir, tag, line)
            callbacks["on_log"](tag, line)
        return run_loop(
            objective=params.objective, seed_tree=Path(params.seed),
            tree_store=LocalTreeStore(run_dir / "trees"), operator=operator,
            hub=HubClient(params.hub), image=params.image, token=params.token,
            owner=params.owner, iterations=params.iterations,
            token_budget=params.token_budget, timeout_s=params.timeout,
            heldout_n=params.heldout_n, max_parallel_evals=params.max_parallel_evals,
            now_fn=_now, report=report, on_episode=callbacks["on_episode"],
            on_state=callbacks["on_state"], on_log=_on_log, workdir=run_dir / "work",
            on_iteration=lambda it, res: runlog.append_metric(
                run_dir, runlog.metric_record(it, res)),
        ) or []

    return EvolvePlan(cfg=cfg, run=run, run_dir=run_dir, rid=rid)
