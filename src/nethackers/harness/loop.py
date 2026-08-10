"""The MAP-Elites loop: select -> mutate -> evaluate -> insert (MVP)."""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nethackers.harness.brief import build_brief
from nethackers.harness.evaluate import evaluate
from nethackers.harness.gate import passes_gate
from nethackers.harness.register import register_win
from nethackers.harness.seeds import dev_spec, heldout_spec
from nethackers.harness.store import LocalTreeStore


@dataclass
class EliteState:
    digest: str
    tree: Path
    dev_fitness: float
    heldout_fitness: float


@dataclass
class IterationResult:
    registered: bool
    reason: str
    dev_fitness: float | None = None
    heldout_fitness: float | None = None
    tokens: int | None = None
    digest: str | None = None


def run_loop(
    *,
    objective: str,
    seed_tree: Path,
    tree_store: LocalTreeStore,
    operator: Any,
    hub: Any,
    image: str,
    token: str,
    owner: str,
    iterations: int,
    token_budget: int,
    timeout_s: float,
    heldout_n: int,
    now_fn: Callable[[], str],
    runner=subprocess.run,
    workdir: Path,
) -> list[IterationResult]:
    dev = dev_spec(objective)
    held = heldout_spec(objective, n=heldout_n, start=1000)
    smoke = heldout_spec(objective, n=1, start=9000)
    character = dev.characters()[0]
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Cold start: the seed (AutoAscend) is the first elite.
    seed_digest = tree_store.save(seed_tree)
    dev_fit0, _ = evaluate(tree_store.path(seed_digest), dev, image, now=now_fn(), runner=runner)
    ho_fit0, _ = evaluate(tree_store.path(seed_digest), held, image, now=now_fn(), runner=runner)
    elite = EliteState(seed_digest, tree_store.path(seed_digest), dev_fit0, ho_fit0)

    results: list[IterationResult] = []
    for k in range(iterations):
        worktree = workdir / f"iter-{k}"
        if worktree.exists():
            shutil.rmtree(worktree)
        shutil.copytree(elite.tree, worktree)

        _, parent_ev = evaluate(elite.tree, dev, image, now=now_fn(), runner=runner)
        brief = build_brief(objective, character, parent_ev)
        op = operator.run(worktree, brief, token_budget=token_budget, timeout_s=timeout_s)

        ok, reason = passes_gate(worktree, elite.digest, smoke_spec=smoke,
                                 image=image, now=now_fn(), runner=runner)
        if not ok:
            results.append(IterationResult(False, f"gate:{reason}", tokens=op.tokens))
            continue

        dev_fit, dev_ev = evaluate(worktree, dev, image, now=now_fn(), runner=runner)
        if dev_fit <= elite.dev_fitness:
            results.append(IterationResult(False, "no-dev-gain", dev_fitness=dev_fit,
                                           tokens=op.tokens))
            continue

        ho_fit, _ = evaluate(worktree, held, image, now=now_fn(), runner=runner)
        if ho_fit <= elite.heldout_fitness:
            results.append(IterationResult(False, "no-heldout-gain",
                                           dev_fitness=dev_fit, heldout_fitness=ho_fit,
                                           tokens=op.tokens))
            continue

        digest = tree_store.save(worktree)
        manifest = json.loads((worktree / "nethackers.solution.json").read_text())
        register_win(hub, token=token, owner=owner, child_manifest=manifest,
                     evidence=dev_ev, parent_digest=elite.digest)
        elite = EliteState(digest, tree_store.path(digest), dev_fit, ho_fit)
        results.append(IterationResult(True, "registered", dev_fitness=dev_fit,
                                       heldout_fitness=ho_fit, tokens=op.tokens, digest=digest))
    return results
