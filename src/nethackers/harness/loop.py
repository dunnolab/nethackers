"""The MAP-Elites loop: cold-start every cell, then pick a random cell,
mutate its elite, smoke-gate, dev-eval, register-all, and insert the child
into every cell it improves. Illumination over a per-identity ``CellArchive``
(harness/archive.py) instead of an island hill-climb -- a single full identity
is just a set of size one, so the same code path drives both."""
from __future__ import annotations

import contextlib
import json
import random
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nethackers.contracts.models import TrajectoryResult
from nethackers.harness import aggregate, refs, select
from nethackers.harness.archive import UNION, Cell, CellArchive
from nethackers.harness.brief import build_brief
from nethackers.harness.evaluate import evaluate
from nethackers.harness.gate import passes_gate
from nethackers.harness.metering import TokenUsage
from nethackers.harness.refs import Attempt
from nethackers.harness.register import register_win
from nethackers.harness.seeds import dev_spec, validation_spec
from nethackers.harness.store import LocalTreeStore
from nethackers.hub.objectives import build_union_spec
from nethackers.hub.selector import resolve
from nethackers.hubclient.auth import AuthError


@dataclass
class IterationResult:
    registered: bool
    reason: str
    dev_fitness: float | None = None
    tokens: int | None = None
    usage: TokenUsage | None = None
    digest: str | None = None
    stopped_reason: str | None = None
    regressions: list[tuple[str, float]] | None = None
    causes: dict[str, int] | None = None
    # None means the win reached the hub; otherwise WHY it didn't (never
    # published, auth failed, or some other hub-side error) -- `registered`
    # now means "the child improved >=1 cell", so this stays the independent
    # record of whether that program actually reached the hub (register-all
    # pushes every scored program, improved or not).
    hub_reason: str | None = None
    # The cells this program became the elite of (identities, plus "union"
    # when it took the overall-average cell) -- the MAP-Elites illumination
    # signal. None for a non-improving/rejected iteration; a non-empty list
    # for a registered win.
    improved: list[str] | None = None


def _causes(results) -> dict[str, int]:
    """Count genuine causes of death across an evaluation's episodes (mirrors
    brief.py's end_status tally, but over the verbatim death string)."""
    return dict(Counter(r.cause_of_death for r in results if r.cause_of_death))


# How many recent EVALUATED attempts (registered or rejected -- anything that
# reached a real dev score) to keep as Attempt records. This caps BOTH the
# /refs/attempts/<n>/ tree copies and the attempts.md score-table rows to the
# most recent few (matching the old per-island <=3 rejected-tree cap). A
# smoke-gate reject never reaches here -- it has no score, so it never becomes
# an Attempt.
_ATTEMPT_REFS_CAP = 3


_HYP = re.compile(r"#\s*hypothesis:\s*(.+)", re.IGNORECASE)


def _hypotheses_in(root: Path) -> list[str]:
    """Every `# hypothesis: …` text in a tree, in sorted path order (a file may
    hold more than one). Best-effort: an unreadable file (encoding issue, race)
    is skipped, not fatal."""
    found: list[str] = []
    for p in sorted(Path(root).rglob("*.py")):
        with contextlib.suppress(OSError, UnicodeDecodeError):
            found.extend(m.group(1).strip() for m in _HYP.finditer(p.read_text()))
    return found


def _hypothesis_of(worktree: Path, parent: Path | None = None) -> str | None:
    """The mutator's OWN `# hypothesis: …` for this mutation. The worktree is a
    copy of the parent elite, so it inherits every ancestor's hypothesis
    comment (typically in an early-sorting file like autoascend/agent.py); the
    old "first match in sorted path order" therefore returned a STALE inherited
    line, not the change just made. So diff against the pristine `parent`: the
    new hypothesis is the first worktree line whose text isn't already in the
    parent. Honest when nothing is new (the mutation added no hypothesis) ->
    None, never an inherited echo. With `parent=None` (no reference) it falls
    back to the first hypothesis found, preserving the standalone helper's old
    contract."""
    worktree_hyps = _hypotheses_in(worktree)
    if parent is None:
        return worktree_hyps[0] if worktree_hyps else None
    inherited = set(_hypotheses_in(parent))
    for hyp in worktree_hyps:
        if hyp not in inherited:
            return hyp
    return None


def _pick_cell(rng: random.Random, archive: CellArchive) -> tuple[str, Cell]:
    """Weighted parent draw over the archive's cells: each identity weight 1,
    the union cell weight 2 -- but only once a full-coverage program has filled
    it. Before that the union cell is empty, so the draw is the original
    uniform-over-identities (identical rng stream to the pre-union behavior).
    Returns ``(label, cell)`` where ``label`` is an identity or ``"union"``."""
    if archive.union is None:
        ident = rng.choice(list(archive.identities))
        return ident, archive.cell(ident)
    labels = list(archive.identities) + [UNION]
    weights = [1] * len(archive.identities) + [2]
    label = rng.choices(labels, weights=weights, k=1)[0]
    return label, (archive.union if label == UNION else archive.cell(label))


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
    from_seed: bool = False,
    fetch: Callable[[dict, Path], Path | None] = select.pull_fetch,
    max_parallel_evals: int = 8,
    max_consecutive_errors: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    stop: threading.Event | None = None,
    now_fn: Callable[[], str],
    runtime: str = "docker",
    report: Callable[[str], None] = lambda _: None,
    on_episode: Callable[[str, dict], None] | None = None,
    on_log: Callable[[str, str], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    on_iteration: Callable[[int, IterationResult], None] = lambda _i, _r: None,
    runner=subprocess.run,
    workdir: Path,
    rng: random.Random | None = None,
    publish: Callable[[Path], dict[str, str] | None] | None = None,
) -> list[IterationResult]:
    rng = rng or random.Random()
    dev = dev_spec(objective)
    resolved = resolve(objective)
    identities = sorted(resolved.identities)          # single => size-1 set
    # A set's smoke check uses ONE member (cheap 1-ep contract check), not
    # |S| episodes -- the full union dev eval below is what actually catches
    # per-build breakage; smoke only guards against an outright crash.
    smoke = validation_spec(identities[0], n=1, start=9000, max_steps=2000)
    character = dev.characters()[0]
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    archive = CellArchive(identities)
    wins = 0
    base_dev = 0.0
    # Run-global attempt history (capped at _ATTEMPT_REFS_CAP, NOT per-cell,
    # NOT cross-run): every evaluated child (registered or rejected), most-
    # recent last. Backs BOTH /refs/attempts/<n>/ (the code + its eval.json)
    # and /refs/attempts.md (the per-identity scores table) -- see refs.py.
    attempts: list[Attempt] = []

    def _remember(label: str, tree: Path, hyp: str | None,
                  per_identity: dict[str, float], overall: float,
                  eval_json: str) -> None:
        attempts.append(Attempt(label, tree, hyp, per_identity, overall, eval_json))
        del attempts[:-_ATTEMPT_REFS_CAP]

    def _episode_cb(label: str) -> Callable[[dict], None] | None:
        # None when the caller isn't rendering -> evaluate stays on the plain
        # (non-streaming) runner path; a bound closure otherwise.
        if on_episode is None:
            return None
        cb = on_episode
        return lambda ep: cb(label, ep)

    def _log_cb(tag: str) -> Callable[[str], None] | None:
        if on_log is None:
            return None
        cb = on_log
        return lambda line: cb(tag, line)

    def _emit(phase: str, iteration: int, *, cell: str | None = None,
              tokens: int = 0, detail: str = "", hub_reason: str | None = None) -> None:
        if on_state is None:
            return
        filled, total = archive.coverage()
        best_dev = max((c.score for c in archive.cells.values()), default=base_dev)
        payload = {
            "phase": phase, "iteration": iteration, "generation": iteration,
            "baseline_dev": base_dev, "baseline_held": 0.0,
            # best_dev/best_held are retained (validation is gone, so held is a
            # constant 0.0) purely so the existing TUI formatters
            # (parent_panel/status_line/lineage_strip) don't KeyError before
            # the C1 cell-archive view lands.
            "best_dev": best_dev, "best_held": 0.0,
            "wins": wins, "tokens": tokens, "detail": detail, "hub_reason": hub_reason,
            "identities": identities, "cell": cell,
            "cells": [{"identity": i, "score": archive.cells[i].score,
                       "digest": archive.cells[i].digest}
                      for i in identities if i in archive.cells],
            "coverage": (filled, total),
            # Parent snapshot: defaults keep the (pre-C1) parent_panel from
            # KeyError-ing before a cell is active; overwritten with the
            # mutated cell's values whenever one is named.
            "parent_digest": "", "parent_dev": base_dev, "parent_held": 0.0,
        }
        c = None
        if cell == UNION:
            c = archive.union
        elif cell is not None and cell in archive.cells:
            c = archive.cells[cell]
        if c is not None:
            payload["parent_digest"] = c.digest
            payload["parent_dev"] = c.score
            if c.dev_evidence is not None:
                payload["parent_means"] = aggregate.per_identity_means(c.dev_evidence.results)
        on_state(payload)

    # Cold start: seed each cell on ITS OWN identity's batch. The champions
    # partition S (per_identity_elites returns one per identity), so score each
    # distinct champion once on the sub-union of the identities it owns, and the
    # seed once on the identities no champion covers. Every identity is scored
    # exactly once -> N*b episodes total, whatever the champion count (the old
    # overlay scored EVERY champion on the full union: (1+P)*N*b). --from-seed
    # keeps the hub out: the seed owns all of S.
    seed_digest = tree_store.save(seed_tree)
    archive.mark_seed(seed_digest)
    elites: dict[str, tuple[dict, Path]] = (
        {} if from_seed
        else select.per_identity_elites(hub, tuple(identities), owner,
                                        store=tree_store, fetch=fetch))
    owned: dict[str, tuple[Path, list[str]]] = {}
    for ident, (entry, tree_path) in elites.items():
        # program_id (not solution_digest -- /elites rows don't carry that;
        # see harness/select.py) -- opaque, but stable per distinct champion,
        # which is all this grouping key needs.
        owned.setdefault(entry["program_id"], (tree_path, []))[1].append(ident)

    frontier_results: list[TrajectoryResult] = []   # every cold-start episode -> baseline tally
    for d, (tree_path, idents) in owned.items():
        report(f"cold-start · scoring {d[:8]} on {len(idents)} cell(s) …")
        spec = build_union_spec(sorted(idents), name=f"coldstart:{d[:8]}")
        _f, ev = evaluate(
            tree_path, spec, image, now=now_fn(), runtime=runtime, runner=runner,
            on_episode=_episode_cb(f"cold-start · dev [{d[:8]}]"),
            max_parallel_evals=max_parallel_evals)
        archive.insert(d, tree_path, ev)
        frontier_results.extend(ev.results)

    seed_idents = [i for i in identities if i not in elites]
    if seed_idents:
        report(f"cold-start · scoring seed on {len(seed_idents)} cell(s) …")
        spec = build_union_spec(sorted(seed_idents), name="coldstart:seed")
        _f, seed_ev = evaluate(
            tree_store.path(seed_digest), spec, image, now=now_fn(), runtime=runtime, runner=runner,
            on_episode=_episode_cb("cold-start · dev [seed]"),
            max_parallel_evals=max_parallel_evals)
        archive.insert(seed_digest, tree_store.path(seed_digest), seed_ev)
        frontier_results.extend(seed_ev.results)

    # base_dev is the frontier the run departs from -- the mean of the cells'
    # starting elite scores -- NOT a separate full-union seed eval (dropped). The
    # AutoAscend reference lives in the hub's isolated baseline, not here.
    base_dev = (sum(c.score for c in archive.cells.values()) / len(archive.cells)
                if archive.cells else 0.0)
    _emit("cold-start", 0)
    on_iteration(0, IterationResult(False, "baseline", dev_fitness=base_dev,
                                    causes=_causes(frontier_results)))
    results: list[IterationResult] = []
    consecutive_errors = 0

    def _record(iteration: int, result: IterationResult) -> None:
        results.append(result)
        on_iteration(iteration, result)

    for k in range(iterations):
        if stop is not None and stop.is_set():
            break  # manual hard-stop: don't start another iteration
        tag = f"iter {k + 1}/{iterations}"
        cell_label, cell = _pick_cell(rng, archive)        # weighted cell draw (union 2x)
        note_hyp: str | None = None
        try:
            worktree = workdir / f"iter-{k}"
            if worktree.exists():
                shutil.rmtree(worktree)
            shutil.copytree(cell.tree, worktree)

            # Hand the TRAINING seeds in as data (spec §3.6): the mutator image
            # has no harness/seeds.py to derive them. parent_means/parent_overall:
            # the mutated cell's own per-identity means / union mean (the base
            # being mutated); target is the run's best full-coverage union score
            # so far (None until the union cell is seeded).
            parent_means = (aggregate.per_identity_means(cell.dev_evidence.results)
                            if cell.dev_evidence is not None else {})
            parent_overall = (aggregate.union_mean(cell.dev_evidence.results)
                              if cell.dev_evidence is not None else None)
            brief = build_brief(
                objective, character,
                identities=identities if len(identities) > 1 else None,
                per_identity=parent_means or None,
                overall=parent_overall,
                target=(archive.union.score if archive.union is not None else None),
                seeds_per_identity=len(dev.batch) // len(identities),
                training_seeds=sorted({s for s, _c in dev.batch}))
            if on_log is not None:
                on_log(tag, json.dumps({"type": "nethackers_brief", "text": brief}))
            # A FRESH `/refs/` dir every iteration (refs.assemble's copytree is
            # dirs_exist_ok=False): a pristine parent copy, its per-seed eval,
            # and the capped `attempts` list rendered as both real code trees
            # (/refs/attempts/<n>/, each with its own eval.json) and a
            # per-identity scores table (/refs/attempts.md).
            refs_dir = workdir / f"refs-{k}"
            refs.assemble(
                refs_dir, parent=cell.tree,
                parent_eval=json.dumps([r.to_dict() for r in cell.dev_evidence.results])
                if cell.dev_evidence is not None else None,
                attempts=list(attempts), identities=identities)

            _emit("mutating", k + 1, cell=cell_label)
            report(f"{tag} · mutating cell {cell_label} …")
            try:
                op = operator.run(worktree, brief, refs=refs_dir,
                                  on_line=_log_cb(tag), stop=stop)
            except Exception as e:
                # run_operator RAISES on a non-zero backend exit / startup
                # failure -- trip a circuit-breaker with backoff rather than
                # letting the generic outer `except` fast-`continue` and spin
                # the whole `iterations` budget against a broken operator.
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
            # Diff against the pristine parent (cell.tree, the copytree source)
            # so this is the mutation's OWN hypothesis, not one inherited from
            # an ancestor and merely carried along in the worktree.
            note_hyp = _hypothesis_of(worktree, cell.tree)

            report(f"{tag} · operator: {op.spend} tok ({op.stopped_reason}); gating…")
            _emit("gating", k + 1, cell=cell_label, tokens=op.spend)
            ok, reason = passes_gate(worktree, cell.digest, smoke_spec=smoke,
                                     image=image, now=now_fn(), runtime=runtime, runner=runner,
                                     on_episode=_episode_cb(f"{tag} · smoke"))
            if not ok:
                _emit("rejected", k + 1, cell=cell_label, tokens=op.spend, detail=f"gate: {reason}")
                report(f"{tag} · ✗ gate: {reason}")
                # No score -> no Attempt: a smoke-gate reject never reaches the
                # mutator via /refs/attempts (nothing to show it).
                _record(k + 1, IterationResult(False, f"gate:{reason}", tokens=op.spend,
                                               usage=op.usage, stopped_reason=op.stopped_reason))
                continue

            _emit("evaluating-dev", k + 1, cell=cell_label, tokens=op.spend)
            report(f"{tag} · gate ok; dev eval ({len(dev.batch)}ep)…")
            dev_fit, dev_ev = evaluate(
                worktree, dev, image, now=now_fn(), runtime=runtime, runner=runner,
                on_episode=_episode_cb(f"{tag} · dev"), max_parallel_evals=max_parallel_evals)
            digest = tree_store.save(worktree)
            manifest = json.loads((worktree / "nethackers.solution.json").read_text())

            # register-all: publish + register EVERY scored program (improved
            # or not). Best-effort -- the child still enters the archive below
            # even if the hub is unreachable; `hub_reason` records WHY it stayed
            # local-only. The hub slices a set win into per-identity atoms
            # server-side (A5), so this is one plain register_win either way.
            hub_ok, hub_reason = True, None
            try:
                reference = publish(worktree) if publish is not None else None
                if reference is None:
                    hub_ok = False
                    hub_reason = "local-only: not published (no gh publisher / dev owner)"
                else:
                    register_win(hub, token=token, child_manifest=manifest,
                                 evidence=dev_ev, parent_digest=cell.digest,
                                 reference=reference)
            except AuthError as e:
                hub_ok = False
                hub_reason = f"local-only: auth failed for run owner '{owner}' — {e}"
                report(f"{tag} · ⚠ hub auth failed: {e}")
            except Exception as e:
                hub_ok = False
                hub_reason = f"local-only: hub error — {e}"
                report(f"{tag} · ⚠ hub publish/register failed: {e}")

            child_means = aggregate.per_identity_means(dev_ev.results)
            child_overall = aggregate.union_mean(dev_ev.results)
            improved = archive.insert(digest, tree_store.path(digest), dev_ev)
            # A rising union mean can still hide a per-identity drop -- diff the
            # mutated cell's parent means against the child's so a regression is
            # surfaced, not absorbed. On a size-1 identity set this is naturally
            # [] when the one identity didn't drop.
            regs = aggregate.regressions(parent_means, child_means)
            # Record for BOTH outcomes -- a rejected-but-scored child is exactly
            # as useful a "don't repeat this" reference as a registered one.
            _remember(str(k + 1), worktree, note_hyp, child_means, child_overall,
                      json.dumps([r.to_dict() for r in dev_ev.results]))
            if improved:
                wins += 1
                _emit("registered", k + 1, cell=cell_label, tokens=op.spend,
                      detail=(f"⚠{len(regs)}" if regs else ""), hub_reason=hub_reason)
                if hub_ok:
                    report(f"{tag} · ✓ REGISTERED dev={dev_fit:.3f} · "
                           f"improved {len(improved)} cell(s)")
                else:
                    report(f"{tag} · ✓ improved {len(improved)} cell(s) "
                           f"dev={dev_fit:.3f} (kept local)")
                _record(k + 1, IterationResult(
                    True, "registered", dev_fitness=dev_fit, tokens=op.spend, usage=op.usage,
                    digest=digest, stopped_reason=op.stopped_reason, regressions=regs or None,
                    causes=_causes(dev_ev.results), hub_reason=hub_reason, improved=improved))
            else:
                _emit("rejected", k + 1, cell=cell_label, tokens=op.spend,
                      detail="no cell improved", hub_reason=hub_reason)
                report(f"{tag} · ✗ improved no cell: dev={dev_fit:.3f}")
                _record(k + 1, IterationResult(
                    False, "no-cell-improved", dev_fitness=dev_fit, tokens=op.spend,
                    usage=op.usage, stopped_reason=op.stopped_reason,
                    causes=_causes(dev_ev.results), hub_reason=hub_reason))
        except Exception as e:
            _emit("error", k + 1, detail=str(e))
            report(f"{tag} · ✗ error: {e}")
            _record(k + 1, IterationResult(False, f"error:{e}"))
            continue
    _emit("done", iterations)
    return results
