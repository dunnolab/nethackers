"""Per-run record helpers (wandb-style): run-id, run.json config, and
metrics.jsonl lines. Pure + injectable so the CLI can stay thin and this
stays unit-testable without a real run."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nethackers.harness.loop import IterationResult

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def run_id(
    now: datetime, name: str | None = None,
    exists: Callable[[str], bool] = lambda _r: False,
) -> str:
    base = now.strftime("%Y%m%d-%H%M%S")
    if name and (s := _slug(name)):
        base = f"{base}-{s}"
    rid, n = base, 1
    while exists(rid):
        rid, n = f"{base}-{n}", n + 1
    return rid


def write_run_config(run_dir: Path, config: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(config, indent=2))


def append_metric(run_dir: Path, record: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "metrics.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")


def append_log(run_dir: Path, tag: str, line: str) -> None:
    """Persist one raw operator-stream line under ``runs/<id>/logs/<tag>.log``
    (tag ``"iter K/N"`` -> file ``iter-k-n.log``), so a run's mutation reasoning
    survives past the live TUI -- the hermetic operator's ``--no-session-
    persistence`` means Claude Code no longer keeps its own transcript. Written
    verbatim (the stream line already carries its newline); a missing trailing
    newline is added so entries stay separated."""
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / f"{_slug(tag)}.log").open("a") as f:
        f.write(line if line.endswith("\n") else line + "\n")


def metric_record(iteration: int, result: IterationResult) -> dict:
    if result.reason == "baseline":
        outcome = "baseline"
    elif result.registered:
        outcome = "registered"
    elif result.reason.startswith(("error", "operator-error")):
        outcome = "error"
    else:
        outcome = "rejected"
    return {
        "iteration": iteration, "outcome": outcome, "reason": result.reason,
        "dev_fitness": result.dev_fitness, "validation_fitness": result.validation_fitness,
        "tokens": result.usage.total if result.usage else result.tokens,
        "usage": asdict(result.usage) if result.usage else None,
        "stopped_reason": result.stopped_reason,
        "child_digest": result.digest,
        "causes": result.causes,
    }
