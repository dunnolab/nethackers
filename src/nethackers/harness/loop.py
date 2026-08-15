"""The MAP-Elites loop: select -> mutate -> evaluate -> insert (MVP)."""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nethackers.contracts.models import Evidence
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
    dev_evidence: Evidence  # cached: the brief reuses it instead of re-scoring the parent


@dataclass
class IterationResult:
    registered: bool
    reason: str
    dev_fitness: float | None = None
    heldout_fitness: float | None = None
    tokens: int | None = None
    digest: str | None = None
    stopped_reason: str | None = None


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
    max_parallel_evals: int = 8,
    now_fn: Callable[[], str],
    report: Callable[[str], None] = lambda _: None,
    on_episode: Callable[[str, dict], None] | None = None,
    on_log: Callable[[str, str], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    on_iteration: Callable[[int, IterationResult], None] = lambda _i, _r: None,
    runner=subprocess.run,
    workdir: Path,
) -> list[IterationResult]:
    dev = dev_spec(objective)
    held = heldout_spec(objective, n=heldout_n, start=1000)
    smoke = heldout_spec(objective, n=1, start=9000, max_steps=2000)
    character = dev.characters()[0]
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    def _episode_cb(label: str) -> Callable[[dict], None] | None:
        # None when the caller isn't rendering -> evaluate stays on the plain
        # (non-streaming) runner path; a bound closure otherwise.
        if on_episode is None:
            return None
        cb = on_episode
        return lambda ep: cb(label, ep)

    wins = 0
    base_dev = base_held = 0.0

    def _log_cb(tag: str) -> Callable[[str], None] | None:
        if on_log is None:
            return None
        cb = on_log
        return lambda line: cb(tag, line)

    def _emit(phase: str, iteration: int, *, tokens: int = 0, detail: str = "") -> None:
        if on_state is None:
            return
        on_state({
            "phase": phase, "iteration": iteration,
            "baseline_dev": base_dev, "baseline_held": base_held,
            "best_dev": elite.dev_fitness, "best_held": elite.heldout_fitness,
            "wins": wins, "tokens": tokens, "detail": detail,
            "parent_digest": parent_digest, "parent_dev": parent_dev,
            "parent_held": parent_held, "generation": generation,
        })

    # Cold start: the seed (AutoAscend) is the first elite.
    report(f"cold-start · scoring seed: dev {len(dev.batch)}ep + held-out {len(held.batch)}ep…")
    seed_digest = tree_store.save(seed_tree)
    dev_fit0, dev_ev0 = evaluate(
        tree_store.path(seed_digest), dev, image, now=now_fn(), runner=runner,
        on_episode=_episode_cb("cold-start · dev"), max_parallel_evals=max_parallel_evals,
    )
    ho_fit0, _ = evaluate(
        tree_store.path(seed_digest), held, image, now=now_fn(), runner=runner,
        on_episode=_episode_cb("cold-start · held-out"), max_parallel_evals=max_parallel_evals,
    )
    elite = EliteState(seed_digest, tree_store.path(seed_digest), dev_fit0, ho_fit0, dev_ev0)
    base_dev, base_held = dev_fit0, ho_fit0
    parent_digest, parent_dev, parent_held = seed_digest, dev_fit0, ho_fit0
    generation = 0
    report(f"cold-start · elite=seed dev={dev_fit0:.3f} held={ho_fit0:.3f}")
    _emit("cold-start", 0)
    on_iteration(0, IterationResult(False, "baseline",
                                    dev_fitness=dev_fit0, heldout_fitness=ho_fit0))

    results: list[IterationResult] = []

    def _record(iteration: int, result: IterationResult) -> None:
        results.append(result)
        on_iteration(iteration, result)

    for k in range(iterations):
        tag = f"iter {k + 1}/{iterations}"
        try:
            parent_digest, parent_dev, parent_held = (
                elite.digest, elite.dev_fitness, elite.heldout_fitness)
            generation = wins

            worktree = workdir / f"iter-{k}"
            if worktree.exists():
                shutil.rmtree(worktree)
            shutil.copytree(elite.tree, worktree)

            brief = build_brief(objective, character, elite.dev_evidence)
            _emit("mutating", k + 1)
            report(f"{tag} · mutating (budget {token_budget} tok)…")
            op = operator.run(worktree, brief, token_budget=token_budget,
                              timeout_s=timeout_s, on_line=_log_cb(tag))
            report(f"{tag} · operator: {op.tokens} tok ({op.stopped_reason}); gating…")
            _emit("gating", k + 1, tokens=op.tokens)

            ok, reason = passes_gate(worktree, elite.digest, smoke_spec=smoke,
                                     image=image, now=now_fn(), runner=runner,
                                     on_episode=_episode_cb(f"{tag} · smoke"))
            if not ok:
                _emit("rejected", k + 1, tokens=op.tokens, detail=f"gate: {reason}")
                report(f"{tag} · ✗ gate: {reason}")
                _record(k + 1, IterationResult(False, f"gate:{reason}", tokens=op.tokens,
                                               stopped_reason=op.stopped_reason))
                continue

            _emit("evaluating-dev", k + 1, tokens=op.tokens)
            report(f"{tag} · gate ok; dev eval ({len(dev.batch)}ep)…")
            dev_fit, dev_ev = evaluate(
                worktree, dev, image, now=now_fn(), runner=runner,
                on_episode=_episode_cb(f"{tag} · dev"), max_parallel_evals=max_parallel_evals,
            )
            if dev_fit <= elite.dev_fitness:
                _emit("rejected", k + 1, tokens=op.tokens, detail="no dev gain")
                report(f"{tag} · ✗ no dev gain: {dev_fit:.3f} ≤ {elite.dev_fitness:.3f}")
                _record(k + 1, IterationResult(False, "no-dev-gain", dev_fitness=dev_fit,
                                               tokens=op.tokens, stopped_reason=op.stopped_reason))
                continue

            _emit("evaluating-held", k + 1, tokens=op.tokens)
            report(f"{tag} · dev win {dev_fit:.3f}; held-out ({len(held.batch)}ep)…")
            ho_fit, _ = evaluate(
                worktree, held, image, now=now_fn(), runner=runner,
                on_episode=_episode_cb(f"{tag} · held-out"), max_parallel_evals=max_parallel_evals,
            )
            if ho_fit <= elite.heldout_fitness:
                _emit("rejected", k + 1, tokens=op.tokens, detail="no held-out gain")
                report(f"{tag} · ✗ no held-out gain: {ho_fit:.3f} ≤ {elite.heldout_fitness:.3f}")
                _record(k + 1, IterationResult(False, "no-heldout-gain",
                                               dev_fitness=dev_fit, heldout_fitness=ho_fit,
                                               tokens=op.tokens, stopped_reason=op.stopped_reason))
                continue

            digest = tree_store.save(worktree)
            manifest = json.loads((worktree / "nethackers.solution.json").read_text())
            register_win(hub, token=token, owner=owner, child_manifest=manifest,
                         evidence=dev_ev, parent_digest=elite.digest)
            elite = EliteState(digest, tree_store.path(digest), dev_fit, ho_fit, dev_ev)
            wins += 1
            _emit("registered", k + 1, tokens=op.tokens)
            report(f"{tag} · ✓ REGISTERED dev={dev_fit:.3f} held={ho_fit:.3f}")
            _record(k + 1, IterationResult(True, "registered", dev_fitness=dev_fit,
                                           heldout_fitness=ho_fit, tokens=op.tokens,
                                           digest=digest, stopped_reason=op.stopped_reason))
        except Exception as e:
            _emit("error", k + 1, detail=str(e))
            report(f"{tag} · ✗ error: {e}")
            _record(k + 1, IterationResult(False, f"error:{e}"))
            continue
    _emit("done", iterations)
    return results
