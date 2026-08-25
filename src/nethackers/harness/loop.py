"""The MAP-Elites loop: select -> mutate -> evaluate -> insert (MVP)."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nethackers.contracts.models import Evidence
from nethackers.harness import aggregate
from nethackers.harness.brief import build_brief
from nethackers.harness.evaluate import evaluate
from nethackers.harness.gate import passes_gate
from nethackers.harness.metering import TokenUsage
from nethackers.harness.register import register_win, register_win_slices
from nethackers.harness.seeds import dev_spec, validation_spec
from nethackers.harness.select import top_trusted_elite
from nethackers.harness.store import LocalTreeStore
from nethackers.hub.selector import resolve


@dataclass
class EliteState:
    digest: str
    tree: Path
    dev_fitness: float
    validation_fitness: float
    dev_evidence: Evidence  # cached: the brief reuses it instead of re-scoring the parent


@dataclass
class IterationResult:
    registered: bool
    reason: str
    dev_fitness: float | None = None
    validation_fitness: float | None = None
    tokens: int | None = None
    usage: TokenUsage | None = None
    digest: str | None = None
    stopped_reason: str | None = None
    regressions: list[tuple[str, float]] | None = None
    causes: dict[str, int] | None = None


def _causes(results) -> dict[str, int]:
    """Count genuine causes of death across an evaluation's episodes (mirrors
    brief.py's end_status tally, but over the verbatim death string)."""
    return dict(Counter(r.cause_of_death for r in results if r.cause_of_death))


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
    validation_n: int,
    max_parallel_evals: int = 8,
    migrate: bool = True,
    max_consecutive_errors: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    stop: threading.Event | None = None,
    now_fn: Callable[[], str],
    report: Callable[[str], None] = lambda _: None,
    on_episode: Callable[[str, dict], None] | None = None,
    on_log: Callable[[str, str], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    on_iteration: Callable[[int, IterationResult], None] = lambda _i, _r: None,
    runner=subprocess.run,
    workdir: Path,
    publish: Callable[[Path], dict[str, str] | None] | None = None,
) -> list[IterationResult]:
    dev = dev_spec(objective)
    resolved = resolve(objective)
    identities = sorted(resolved.identities) if resolved.kind == "set" else []
    validation = validation_spec(objective, n=validation_n, start=1000)
    # A set's smoke check uses ONE member (cheap 1-ep contract check), not
    # |S| episodes -- the full union dev eval below is what actually catches
    # per-build breakage; smoke only guards against an outright crash.
    smoke = validation_spec(
        identities[0] if identities else objective, n=1, start=9000, max_steps=2000)
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
    base_dev = base_validation = 0.0

    def _log_cb(tag: str) -> Callable[[str], None] | None:
        if on_log is None:
            return None
        cb = on_log
        return lambda line: cb(tag, line)

    def _emit(phase: str, iteration: int, *, tokens: int = 0, detail: str = "") -> None:
        if on_state is None:
            return
        payload = {
            "phase": phase, "iteration": iteration,
            "baseline_dev": base_dev, "baseline_held": base_validation,
            "best_dev": elite.dev_fitness, "best_held": elite.validation_fitness,
            "wins": wins, "tokens": tokens, "detail": detail,
            # parent snapshot for the monitor's PARENT panel + lineage chain
            # (additive; the in-flight child's digest isn't known here).
            # generation = the iteration being worked (advances every attempt),
            # not wins -- so the monitor's `gen` visibly moves even before a win.
            "parent_digest": elite.digest, "parent_dev": elite.dev_fitness,
            "parent_held": elite.validation_fitness, "generation": iteration,
        }
        if identities:
            # Live from the CURRENT elite (a closure var reassigned on a win
            # or migration) -- never memoized -- so the parent snapshot
            # doesn't go stale after either event.
            pm = aggregate.per_identity_means(elite.dev_evidence.results)
            payload.update({
                "identities": identities,
                "parent_means": pm,
                "coverage": aggregate.coverage(pm, identities),
            })
        on_state(payload)

    def _score_elite(tree_path: Path, digest: str, *, dev_label: str,
                     val_label: str) -> EliteState:
        # Score a tree on this run's own dev+validation specs -> an EliteState.
        # Shared by the cold-start (seed baseline) and mid-run migration, so an
        # adopted elite's gate thresholds are established identically.
        dev_fit, dev_ev = evaluate(
            tree_path, dev, image, now=now_fn(), runner=runner,
            on_episode=_episode_cb(dev_label), max_parallel_evals=max_parallel_evals)
        val_fit, _ = evaluate(
            tree_path, validation, image, now=now_fn(), runner=runner,
            on_episode=_episode_cb(val_label), max_parallel_evals=max_parallel_evals)
        return EliteState(digest, tree_path, dev_fit, val_fit, dev_ev)

    # Cold start: the seed (AutoAscend) is the first elite.
    report(f"cold-start · scoring seed: dev {len(dev.batch)}ep "
           f"+ validation {len(validation.batch)}ep…")
    seed_digest = tree_store.save(seed_tree)
    elite = _score_elite(tree_store.path(seed_digest), seed_digest,
                         dev_label="cold-start · dev", val_label="cold-start · validation")
    base_dev, base_validation = elite.dev_fitness, elite.validation_fitness
    report(f"cold-start · elite=seed dev={elite.dev_fitness:.3f} "
           f"validation={elite.validation_fitness:.3f}")
    _emit("cold-start", 0)
    on_iteration(0, IterationResult(False, "baseline",
                                    dev_fitness=elite.dev_fitness,
                                    validation_fitness=elite.validation_fitness,
                                    causes=_causes(elite.dev_evidence.results)))

    results: list[IterationResult] = []
    consecutive_errors = 0

    def _record(iteration: int, result: IterationResult) -> None:
        results.append(result)
        on_iteration(iteration, result)

    for k in range(iterations):
        if stop is not None and stop.is_set():
            break  # manual hard-stop: don't start another iteration
        tag = f"iter {k + 1}/{iterations}"
        try:
            # Mid-run migration: adopt another process's strictly-better elite
            # from the hub before mutating (greedy move-up; digest-differ + a
            # strictly-higher score, which equals dev_fitness for identity
            # objectives). Any failure -> keep the local elite.
            if migrate:
                picked = top_trusted_elite(hub, objective, tree_store, owner)
                if picked is not None:
                    entry, tree_path = picked
                    if (entry["solution_digest"] != elite.digest
                            and entry["score"] > elite.dev_fitness):
                        detail = f"{entry['owner']}/{entry['solution_digest'][:12]}"
                        report(f"{tag} · ↥ migrating → {detail} "
                               f"(score {entry['score']:.3f} > {elite.dev_fitness:.3f})")
                        if on_log is not None:
                            on_log(tag, f"migrated ← {detail} score "
                                        f"{entry['score']:.3f} > {elite.dev_fitness:.3f}\n")
                        elite = _score_elite(
                            tree_path, entry["solution_digest"],
                            dev_label=f"{tag} · migrate-dev",
                            val_label=f"{tag} · migrate-validation")
                        _emit("migrated", k + 1, detail=detail)

            worktree = workdir / f"iter-{k}"
            if worktree.exists():
                shutil.rmtree(worktree)
            shutil.copytree(elite.tree, worktree)

            # Hand the TRAINING seeds in as data (spec §3.6): the mutator image
            # has no harness/seeds.py to derive them, so the brief is where it
            # learns which seeds to develop against -- never the held-out ones.
            # parent_means: live from the CURRENT elite, same rule as _emit.
            parent_means = (aggregate.per_identity_means(elite.dev_evidence.results)
                            if identities else {})
            brief = build_brief(objective, character, elite.dev_evidence,
                                training_seeds=sorted({s for s, _c in dev.batch}),
                                identities=identities or None,
                                per_identity=parent_means or None)
            # Head the iteration's log with the brief it was given, so a reader
            # sees what the mutator was asked to do (persisted to the run log +
            # rendered in the TUI's agent-log via prettify's brief event).
            if on_log is not None:
                on_log(tag, json.dumps({"type": "nethackers_brief", "text": brief}))
            _emit("mutating", k + 1)
            report(f"{tag} · mutating…")
            try:
                op = operator.run(worktree, brief, on_line=_log_cb(tag), stop=stop)
            except Exception as e:
                # run_operator RAISES on a non-zero backend exit / startup failure
                # (missing or renamed binary, unavailable model, stale CLI, auth),
                # carrying the CLI's real error. Trip a circuit-breaker with
                # backoff rather than letting the generic outer `except` fast-
                # `continue` -- which would spin the whole `iterations` budget in
                # milliseconds against a persistently-broken operator. A HEALTHY
                # run resets the counter below, so an ordinary no-improvement
                # iteration never trips the breaker.
                consecutive_errors += 1
                detail = str(e)
                _emit("error", k + 1, detail=detail)
                report(f"{tag} · ✗ operator error: {detail}")
                _record(k + 1, IterationResult(False, f"operator-error:{detail}"))
                if consecutive_errors >= max_consecutive_errors:
                    _emit("aborted", k + 1, detail=detail)
                    report(f"{tag} · ✗✗ aborting after {consecutive_errors} "
                           f"consecutive operator failures — last: {detail}")
                    break
                sleep(min(2 ** (consecutive_errors - 1), 30))   # 1s, 2s, 4s… capped
                continue
            consecutive_errors = 0   # a healthy operator run resets the breaker

            report(f"{tag} · operator: {op.total} tok ({op.stopped_reason}); gating…")
            _emit("gating", k + 1, tokens=op.total)

            ok, reason = passes_gate(worktree, elite.digest, smoke_spec=smoke,
                                     image=image, now=now_fn(), runner=runner,
                                     on_episode=_episode_cb(f"{tag} · smoke"))
            if not ok:
                _emit("rejected", k + 1, tokens=op.total, detail=f"gate: {reason}")
                report(f"{tag} · ✗ gate: {reason}")
                _record(k + 1, IterationResult(False, f"gate:{reason}", tokens=op.total,
                                               usage=op.usage,
                                               stopped_reason=op.stopped_reason))
                continue

            _emit("evaluating-dev", k + 1, tokens=op.total)
            report(f"{tag} · gate ok; dev eval ({len(dev.batch)}ep)…")
            dev_fit, dev_ev = evaluate(
                worktree, dev, image, now=now_fn(), runner=runner,
                on_episode=_episode_cb(f"{tag} · dev"), max_parallel_evals=max_parallel_evals,
            )
            if dev_fit <= elite.dev_fitness:
                _emit("rejected", k + 1, tokens=op.total, detail="no dev gain")
                report(f"{tag} · ✗ no dev gain: {dev_fit:.3f} ≤ {elite.dev_fitness:.3f}")
                _record(k + 1, IterationResult(False, "no-dev-gain", dev_fitness=dev_fit,
                                               tokens=op.total, usage=op.usage,
                                               stopped_reason=op.stopped_reason,
                                               causes=_causes(dev_ev.results)))
                continue

            _emit("evaluating-held", k + 1, tokens=op.total)
            report(f"{tag} · dev win {dev_fit:.3f}; validation ({len(validation.batch)}ep)…")
            val_fit, _ = evaluate(
                worktree, validation, image, now=now_fn(), runner=runner,
                on_episode=_episode_cb(f"{tag} · validation"),
                max_parallel_evals=max_parallel_evals,
            )
            if val_fit <= elite.validation_fitness:
                _emit("rejected", k + 1, tokens=op.total, detail="no validation gain")
                report(f"{tag} · ✗ no validation gain: {val_fit:.3f} "
                       f"≤ {elite.validation_fitness:.3f}")
                _record(k + 1, IterationResult(False, "no-validation-gain",
                                               dev_fitness=dev_fit, validation_fitness=val_fit,
                                               tokens=op.total, usage=op.usage,
                                               stopped_reason=op.stopped_reason,
                                               causes=_causes(dev_ev.results)))
                continue

            digest = tree_store.save(worktree)
            manifest = json.loads((worktree / "nethackers.solution.json").read_text())
            # Publish + register are BEST-EFFORT. A validation-confirmed win
            # ALWAYS becomes the local elite below, even if the hub is
            # unreachable or rejects the registration -- real, validated progress
            # is never discarded over a hub-side failure (the next win just
            # re-publishes from the advanced elite). Publish to a real repo@commit
            # (fetchable, passes the hub's commit-exists check) then register the
            # self-reported evidence; no publisher (or any failure) -> local elite
            # only, never a synthetic, unfetchable hub reference.
            hub_ok = True
            try:
                reference = publish(worktree) if publish is not None else None
                if reference is None:
                    hub_ok = False
                    report(f"{tag} · ✓ new local elite (not published to the hub)")
                elif identities:
                    register_win_slices(hub, token=token, child_manifest=manifest,
                                        evidence=dev_ev, identities=identities,
                                        parent_digest=elite.digest, reference=reference)
                else:
                    register_win(hub, token=token, child_manifest=manifest,
                                 evidence=dev_ev, parent_digest=elite.digest,
                                 reference=reference)
            except Exception as e:
                hub_ok = False
                report(f"{tag} · ⚠ win kept as a local elite; hub publish/register "
                       f"failed: {e}")
            # A rising union mean can still hide a per-identity drop on a set
            # objective -- diff the OLD parent (elite, not yet reassigned)
            # against the winning child so a regression is surfaced, not
            # silently absorbed into the aggregate win (spec decision C §5/§9).
            regs = (aggregate.regressions(
                        aggregate.per_identity_means(elite.dev_evidence.results),   # OLD parent
                        aggregate.per_identity_means(dev_ev.results))               # winning child
                    if identities else [])
            elite = EliteState(digest, tree_store.path(digest), dev_fit, val_fit, dev_ev)
            wins += 1
            _emit("registered", k + 1, tokens=op.total, detail=(f"⚠{len(regs)}" if regs else ""))
            if hub_ok:
                report(f"{tag} · ✓ REGISTERED dev={dev_fit:.3f} validation={val_fit:.3f}")
            _record(k + 1, IterationResult(True, "registered", dev_fitness=dev_fit,
                                           validation_fitness=val_fit, tokens=op.total,
                                           usage=op.usage, digest=digest,
                                           stopped_reason=op.stopped_reason,
                                           regressions=regs or None,
                                           causes=_causes(dev_ev.results)))
        except Exception as e:
            _emit("error", k + 1, detail=str(e))
            report(f"{tag} · ✗ error: {e}")
            _record(k + 1, IterationResult(False, f"error:{e}"))
            continue
    _emit("done", iterations)
    return results
