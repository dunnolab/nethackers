"""The MAP-Elites loop: select -> mutate -> evaluate -> insert (MVP)."""
from __future__ import annotations

import contextlib
import json
import math
import random
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nethackers.contracts.models import Evidence
from nethackers.harness import aggregate, refs, select
from nethackers.harness.brief import build_brief
from nethackers.harness.evaluate import evaluate
from nethackers.harness.gate import passes_gate
from nethackers.harness.metering import TokenUsage
from nethackers.harness.register import register_win, register_win_slices
from nethackers.harness.seeds import dev_spec, validation_spec
from nethackers.harness.store import LocalTreeStore
from nethackers.hub.selector import resolve
from nethackers.hubclient.auth import AuthError


@dataclass
class EliteState:
    digest: str
    tree: Path
    dev_fitness: float
    validation_fitness: float
    dev_evidence: Evidence  # cached: the brief reuses it instead of re-scoring the parent
    # Rejected attempts since THIS island's champion became current --
    # provisioned under the next iteration's `/refs/attempts/` so the
    # mutator can see what already failed for THIS lineage. Bounded <=3
    # (oldest dropped first); implicitly cleared on a win because the
    # winning branch below constructs a fresh EliteState without passing it.
    # Each island holds its own list (never shared) -- that per-island
    # isolation of state IS the diversity mechanism islands exist for
    # (spec §4: "isolation is of state, not information").
    recent_attempts: list[refs.Ref] = field(default_factory=list)


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
    # None means the win reached the hub; otherwise WHY it didn't (never
    # published, auth failed, or some other hub-side error) -- `registered`
    # alone only ever meant "the LOCAL win was accepted", so this is what
    # distinguishes a hub-registered win from a local-only one instead of
    # swallowing the difference into a report()-only line (runlog.metric_record
    # + the monitor ledger both surface it).
    hub_reason: str | None = None


def _causes(results) -> dict[str, int]:
    """Count genuine causes of death across an evaluation's episodes (mirrors
    brief.py's end_status tally, but over the verbatim death string)."""
    return dict(Counter(r.cause_of_death for r in results if r.cause_of_death))


_HYP = re.compile(r"#\s*hypothesis:\s*(.+)", re.IGNORECASE)


def _hypothesis_of(worktree: Path) -> str | None:
    """The mutator's own `# hypothesis: …` comment (brief.py asks for one at
    the edit) -- the first match across the worktree's Python files, in
    sorted path order. Best-effort: an unreadable file (encoding issue, race)
    is skipped, not fatal; no match anywhere yields None."""
    for p in sorted(Path(worktree).rglob("*.py")):
        with contextlib.suppress(OSError, UnicodeDecodeError):
            m = _HYP.search(p.read_text())
            if m:
                return m.group(1).strip()
    return None


def select_reseed(survivors: list[EliteState], rng: random.Random,
                  temperature: float = 1.0) -> EliteState:
    """Choose a survivor to reseed a killed island's champion from (Task B2).

    A temperature-weighted sample over the survivors -- P proportional to
    ``exp(dev_fitness / temperature)`` -- reusing ``select._sample``'s
    top-k-sampling formula. NEVER a deterministic argmax: a fitter survivor
    is more likely to be picked but not guaranteed, because always
    reseeding from "the single best" would collapse every killed island
    onto one lineage at every reset and erase the per-island diversity
    islands exist for (spec §4). The caller passes only the survivors
    (already the top half after the kill), so every candidate here is
    already eligible -- that surviving half IS the "top-k" pool.
    """
    if len(survivors) == 1:
        return survivors[0]   # nothing to sample over
    weights = [math.exp(s.dev_fitness / temperature) for s in survivors]
    return rng.choices(survivors, weights=weights, k=1)[0]


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
    islands: int = 1,
    reset_period: int | None = None,
    max_parallel_evals: int = 8,
    from_seed: bool = False,  # deliberate cold start: ignore the hub for island
    #                           seeding AND /refs influences (a hub-independent run)
    fetch: Callable[[dict, Path], Path | None] = select.pull_fetch,
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
    if islands < 1:
        raise ValueError(f"islands must be >= 1, got {islands}")
    if reset_period is not None and reset_period < 1:
        raise ValueError(f"reset_period must be >= 1 when set, got {reset_period}")
    dev = dev_spec(objective)
    resolved = resolve(objective)
    # A single full identity IS a set of size one -- resolve() already carries
    # it in resolved.identities -- so the pool machinery (island seeding, /refs
    # influences, reset injection) and the per-identity brief/monitor treat it
    # uniformly with a multi-identity set; only "random"/"all" have no fixed
    # identity to pool over. `is_set` stays SEPARATE for the registration split:
    # a true set records a per-identity slice per member, a single records one
    # ordinary win (unchanged hub semantics) -- that distinction is genuinely
    # kind-based, unlike the pool, which just needs the identities.
    identities = sorted(resolved.identities) if resolved.kind in ("single", "set") else []
    is_set = resolved.kind == "set"
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

    def _emit(phase: str, iteration: int, *, tokens: int = 0, detail: str = "",
              hub_reason: str | None = None) -> None:
        if on_state is None:
            return
        active = island_states[idx]
        payload = {
            "phase": phase, "iteration": iteration,
            "baseline_dev": base_dev, "baseline_held": base_validation,
            "best_dev": active.dev_fitness, "best_held": active.validation_fitness,
            "wins": wins, "tokens": tokens, "detail": detail, "hub_reason": hub_reason,
            # parent snapshot for the monitor's PARENT panel + lineage chain
            # (additive; the in-flight child's digest isn't known here).
            # generation = the iteration being worked (advances every attempt),
            # not wins -- so the monitor's `gen` visibly moves even before a win.
            "parent_digest": active.digest, "parent_dev": active.dev_fitness,
            "parent_held": active.validation_fitness, "generation": iteration,
            "islands": islands,
        }
        if islands > 1:
            # Per-island snapshot so the monitor can show all K champions, not
            # just the round-robin-active one (which flips every iteration and
            # made a multi-island run look like a single flat parent). Only
            # emitted for islands>1; a single-island run keeps the plain
            # single-lineage view unchanged.
            payload["active_island"] = idx
            payload["reset_period"] = reset_period
            payload["island_champions"] = [
                {"digest": s.digest, "dev": s.dev_fitness, "held": s.validation_fitness}
                for s in island_states
            ]
        if identities:
            # Live from the ACTIVE island's champion (island_states[idx], a
            # closure var whose slot is reassigned on a win) -- never
            # memoized -- so the parent snapshot doesn't go stale after one.
            pm = aggregate.per_identity_means(active.dev_evidence.results)
            payload.update({
                "identities": identities,
                "parent_means": pm,
                "coverage": aggregate.coverage(pm, identities),
            })
        on_state(payload)

    def _score_elite(tree_path: Path, digest: str, *, dev_label: str,
                     val_label: str) -> EliteState:
        # Score a tree on this run's own dev+validation specs -> an EliteState.
        # Used once, at cold start, to establish the seed baseline that every
        # island copies below (so all islands' gate thresholds start identical).
        dev_fit, dev_ev = evaluate(
            tree_path, dev, image, now=now_fn(), runner=runner,
            on_episode=_episode_cb(dev_label), max_parallel_evals=max_parallel_evals)
        val_fit, _ = evaluate(
            tree_path, validation, image, now=now_fn(), runner=runner,
            on_episode=_episode_cb(val_label), max_parallel_evals=max_parallel_evals)
        return EliteState(digest, tree_path, dev_fit, val_fit, dev_ev)

    # Cold start: the seed (AutoAscend) is the first elite. Every island is
    # seeded from this SAME scored tree (they diverge thereafter by
    # independent mutation) -- a fresh EliteState per island so each gets its
    # own `recent_attempts` list (never aliased across islands). B2's reset
    # and C3's influence-pool seeding replace this per-island later; K=1
    # (the default) makes this indistinguishable from the old single-elite
    # cold start.
    report(f"cold-start · scoring seed: dev {len(dev.batch)}ep "
           f"+ validation {len(validation.batch)}ep…")
    seed_digest = tree_store.save(seed_tree)
    seed_elite = _score_elite(tree_store.path(seed_digest), seed_digest,
                              dev_label="cold-start · dev", val_label="cold-start · validation")
    base_dev, base_validation = seed_elite.dev_fitness, seed_elite.validation_fitness
    report(f"cold-start · elite=seed dev={seed_elite.dev_fitness:.3f} "
           f"validation={seed_elite.validation_fitness:.3f}")
    island_states: list[EliteState] = [
        EliteState(seed_elite.digest, seed_elite.tree, seed_elite.dev_fitness,
                   seed_elite.validation_fitness, seed_elite.dev_evidence)
        for _ in range(islands)
    ]
    pool_rng = random.Random()
    if identities and not from_seed and islands > 1:
        # C3: seed the K>1 islands from the coverage-aware influence pool
        # instead of all-K-copies-of-the-seed -- but ONLY if the pool
        # actually has something real to offer. An empty pool (hub down, or
        # no trusted specialist yet for any member identity) leaves
        # `island_states` exactly as built above -- sample_seeds pads every
        # slot with `(seed_tree, None)` in that case, so `samples` below is
        # all-pads and the `any(...)` guard skips straight past.
        # Carve-outs: at K=1 the single island stays `seed_tree` -- in
        # production that is launch's SELECT parent (coverage-gated for a set,
        # the identity's top elite for a single), so pool-seeding
        # would only trade the balanced parent for a lone union-argmax
        # specialist AND pay a redundant 2nd eval to re-score it; --from-seed
        # skips the hub entirely for a deliberate cold start. Both cases still
        # get /refs influences via `_pick_influences` below (except --from-seed).
        samples = select.sample_seeds(hub, tuple(identities), tree_store, seed_tree,
                                      owner=owner, n=islands, fetch=fetch, rng=pool_rng)
        if any(entry is not None for _, entry in samples):
            # Score each DISTINCT tree only once -- sample_seeds already
            # guarantees distinct digests among its resolved (non-pad)
            # entries, so the only repeat here is the pad case: pre-seeding
            # the cache with the already-scored seed_elite means every
            # `(seed_tree, None)` pad (a thin pool, once distinct entries
            # run out) costs nothing extra, reusing seed_elite per C3's
            # "else the seed digest" rule instead of re-scoring it.
            scored: dict[str, EliteState] = {seed_elite.digest: seed_elite}
            seeded_states: list[EliteState] = []
            for tree_path, entry in samples:
                if entry is None:
                    es = seed_elite
                else:
                    digest = entry["solution_digest"]
                    if digest not in scored:
                        scored[digest] = _score_elite(
                            tree_path, digest,
                            dev_label=f"cold-start · dev [{digest[:8]}]",
                            val_label=f"cold-start · validation [{digest[:8]}]")
                    es = scored[digest]
                # A FRESH EliteState per island (never the cached object
                # itself) so each island's `recent_attempts` starts as its
                # own empty list -- same aliasing concern as B2's reset.
                seeded_states.append(EliteState(
                    es.digest, es.tree, es.dev_fitness, es.validation_fitness, es.dev_evidence))
            island_states = seeded_states
            report(f"cold-start · seeded "
                   f"{sum(1 for _, e in samples if e is not None)}/{islands} "
                   f"island(s) from the hub's influence pool")
    idx = 0   # round-robin cursor: iteration k works island_states[k % islands]
    if reset_period is None:
        reset_period = 4 * islands
    reset_rng = random.Random()
    _emit("cold-start", 0)
    on_iteration(0, IterationResult(False, "baseline",
                                    dev_fitness=seed_elite.dev_fitness,
                                    validation_fitness=seed_elite.validation_fitness,
                                    causes=_causes(seed_elite.dev_evidence.results)))

    results: list[IterationResult] = []
    consecutive_errors = 0

    def _record(iteration: int, result: IterationResult) -> None:
        results.append(result)
        on_iteration(iteration, result)

    def _track_rejected_attempt(worktree: Path, note: str) -> None:
        # island_states[idx] is the ACTIVE island's champion (read via
        # closure, like `_emit`) -- appending here, before any reassignment,
        # is what makes a win implicitly clear the list: the win branch below
        # constructs a fresh EliteState that doesn't carry `recent_attempts`
        # forward.
        active = island_states[idx]
        # Prepend the mutator's REAL hypothesis (its `# hypothesis: …` comment
        # in the worktree) when one is present -- `note` itself stays exactly
        # as callers build it (score/outcome summary), so a revisiting island
        # sees both the actual idea tried and the outcome, not just the latter
        # mislabeled as the former.
        hyp = _hypothesis_of(worktree)
        full_note = f"hypothesis: {hyp}; {note}" if hyp else note
        active.recent_attempts.append((f"iter-{k + 1}", worktree, full_note))
        del active.recent_attempts[:-3]   # bounded <=3 -- drop oldest first

    def _hub_reseed_candidate(held: set[str]) -> EliteState | None:
        # Cross-run injection at reset: draw a batch from the same coverage-
        # aware influence pool the islands seed from, and return the first
        # elite NO current island already holds -- scored on THIS run's
        # dev+validation specs so it slots in as an ordinary champion. This is
        # the gentle replacement for the removed mid-run migration: instead of
        # hot-swapping a running parent, a run periodically imports another
        # run's registered progress into a slot it was already discarding.
        # None when the pool offers nothing fresh (hub down, thin pool, or
        # every draw is already held) -> caller reseeds that slot locally.
        # Batch of 2*islands so there's headroom past the (<=islands) held
        # digests; k=islands makes the draw a weighted sample, not the argmax,
        # so successive resets don't keep importing the single same best.
        draws = select.sample_seeds(hub, tuple(identities), tree_store, seed_tree,
                                    owner=owner, n=2 * islands, k=islands,
                                    fetch=fetch, rng=reset_rng)
        for tree_path, entry in draws:
            if entry is None:
                continue   # a pad (thin pool) -- nothing to inject
            digest = entry["solution_digest"]
            if digest in held:
                continue   # an island already has this -- keep looking for fresh
            return _score_elite(tree_path, digest,
                                dev_label=f"reset · dev [{digest[:8]}]",
                                val_label=f"reset · validation [{digest[:8]}]")
        return None

    def _reset_islands() -> None:
        # Periodic reset (Task B2): rank by CHAMPION dev_fitness and kill
        # the bottom half (floor(K/2) -- the call site below only invokes
        # this when islands > 1, so there's always >=1 to kill). Each killed
        # slot is reseeded from a top-k-sampled SURVIVOR (select_reseed) --
        # never deterministically "the best" -- and every survivor's slot
        # (champion AND recent_attempts) is left completely untouched: that
        # per-island state isolation across a reset is what preserves
        # diversity (spec §4), not just at cold-start.
        #
        # Cross-run injection: the FIRST killed slot is instead reseeded from a
        # fresh hub-pool elite when one is available -- at most ONE slot per
        # reset (the rest reseed locally), only for objectives with a fixed
        # identity (single or set, where the pool applies) and never under
        # --from-seed, so a run periodically imports outside progress without
        # collapsing its islands onto one shared hub-best.
        ranked = sorted(range(len(island_states)), key=lambda i: island_states[i].dev_fitness)
        n_kill = len(island_states) // 2
        dead, alive = ranked[:n_kill], ranked[n_kill:]
        survivors = [island_states[i] for i in alive]
        held = {s.digest for s in island_states}
        injected = (_hub_reseed_candidate(held)
                    if (identities and not from_seed) else None)
        for pos, i in enumerate(dead):
            src = injected if (pos == 0 and injected is not None) \
                else select_reseed(survivors, reset_rng)
            # A FRESH EliteState -- never the survivor's/injected object -- so
            # the reseeded slot's recent_attempts starts empty (the dataclass's
            # default_factory) instead of aliasing another slot's list (which
            # would let a later reject on the reseeded island corrupt it).
            island_states[i] = EliteState(
                src.digest, src.tree, src.dev_fitness,
                src.validation_fitness, src.dev_evidence)

    def _pick_influences(active_digest: str) -> list[refs.Ref]:
        # C3: up to 2 cross-elite influences for THIS iteration's `/refs/`,
        # drawn from the same coverage-aware pool the islands are seeded
        # from -- NEVER the active island's own champion (that's the base
        # being mutated, not an outside influence). An objective with no fixed
        # identity (random/all), a --from-seed cold start, or a pool with
        # nothing else to offer yields [] -- refs.assemble already degrades
        # gracefully to no `influences/` folder at all.
        if not identities or from_seed:
            return []
        candidates = select.sample_seeds(hub, tuple(identities), tree_store, seed_tree,
                                         owner=owner, n=2, fetch=fetch, rng=pool_rng)
        picked: list[refs.Ref] = []
        for tree_path, entry in candidates:
            if entry is None:
                continue   # a pad (thin pool) -- not a real influence
            digest = entry["solution_digest"]
            if digest == active_digest:
                continue   # never influence a champion with itself
            # The label names a `/refs/influences/<label>/` folder, so it MUST
            # be unique per influence. Atom digests ("host/owner/repo@sha")
            # share a long common prefix, so digest[:12] is IDENTICAL across a
            # single owner's elites (e.g. "github.com/v") -- two influences
            # would collide on the same folder and refs._copy_refs's copytree
            # would raise FileExistsError, silently burning the iteration. A
            # positional index makes the label collision-proof regardless of
            # digest shape; the sanitized prefix (same policy as
            # LocalTreeStore._key, so a "/" can't nest the folder) stays for
            # legibility, and the full digest moves into the note so the
            # mutator can still tell two same-owner influences apart.
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", digest[:12])
            picked.append((f"hub-{len(picked)}-{safe}", tree_path,
                           f"{digest}; score {entry['score']:.3f}; "
                           f"strong at {entry['identity']}"))
        return picked[:2]

    for k in range(iterations):
        if stop is not None and stop.is_set():
            break  # manual hard-stop: don't start another iteration
        tag = f"iter {k + 1}/{iterations}"
        idx = k % islands   # this iteration's active island
        try:
            # Mid-run migration (adopt another process's strictly-better elite
            # from the hub before mutating) was REMOVED: it operated on a
            # single shared `elite` and cannot coexist with per-island
            # champions (a hub-wide "best" would collapse every island onto
            # one lineage, destroying the diversity islands exist for). The
            # islands reset (kill-bottom-half + reseed from a diverse local
            # survivor) plus the coverage-aware influence pool's occasional
            # cross-run injection at reset subsume it per spec §4.

            worktree = workdir / f"iter-{k}"
            if worktree.exists():
                shutil.rmtree(worktree)
            active = island_states[idx]
            shutil.copytree(active.tree, worktree)

            # Hand the TRAINING seeds in as data (spec §3.6): the mutator image
            # has no harness/seeds.py to derive them, so the brief is where it
            # learns which seeds to develop against -- never the held-out ones.
            # parent_means: live from the ACTIVE island's champion, same rule as _emit.
            parent_means = (aggregate.per_identity_means(active.dev_evidence.results)
                            if identities else {})
            brief = build_brief(objective, character, active.dev_evidence,
                                training_seeds=sorted({s for s, _c in dev.batch}),
                                identities=identities or None,
                                per_identity=parent_means or None)
            # Head the iteration's log with the brief it was given, so a reader
            # sees what the mutator was asked to do (persisted to the run log +
            # rendered in the TUI's agent-log via prettify's brief event).
            if on_log is not None:
                on_log(tag, json.dumps({"type": "nethackers_brief", "text": brief}))
            # Assemble a FRESH `/refs/` dir every iteration (refs.assemble's
            # shutil.copytree is dirs_exist_ok=False -- reusing one path
            # across iterations would raise FileExistsError on the 2nd
            # attempts/<label> that collides). influences: up to 2 OTHER
            # cross-elite pool solutions (Task C3) -- [] for a non-set
            # objective or a pool with nothing else to offer.
            refs_dir = workdir / f"refs-{k}"
            refs.assemble(
                refs_dir,
                base_eval=json.dumps([r.to_dict() for r in active.dev_evidence.results]),
                influences=_pick_influences(active.digest),
                attempts=active.recent_attempts,
            )
            _emit("mutating", k + 1)
            report(f"{tag} · mutating…")
            try:
                op = operator.run(worktree, brief, refs=refs_dir, on_line=_log_cb(tag), stop=stop)
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

            ok, reason = passes_gate(worktree, active.digest, smoke_spec=smoke,
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
            if dev_fit <= active.dev_fitness:
                _emit("rejected", k + 1, tokens=op.total, detail="no dev gain")
                report(f"{tag} · ✗ no dev gain: {dev_fit:.3f} ≤ {active.dev_fitness:.3f}")
                _track_rejected_attempt(
                    worktree,
                    f"score dev={dev_fit:.3f} (parent {active.dev_fitness:.3f}); "
                    f"outcome: {aggregate.outcome_summary(dev_ev.results)}")
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
            if val_fit <= active.validation_fitness:
                _emit("rejected", k + 1, tokens=op.total, detail="no validation gain")
                report(f"{tag} · ✗ no validation gain: {val_fit:.3f} "
                       f"≤ {active.validation_fitness:.3f}")
                _track_rejected_attempt(
                    worktree,
                    f"score dev={dev_fit:.3f} validation={val_fit:.3f} "
                    f"(parent validation {active.validation_fitness:.3f}); "
                    f"outcome: {aggregate.outcome_summary(dev_ev.results)}")
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
            # only, never a synthetic, unfetchable hub reference. `hub_reason`
            # records WHY a local-only win never reached the hub -- never
            # swallowed silently (runlog.metric_record + the monitor ledger both
            # surface it) -- while `registered=True` keeps meaning "the local
            # win was accepted" regardless.
            hub_ok = True
            hub_reason: str | None = None
            try:
                reference = publish(worktree) if publish is not None else None
                if reference is None:
                    hub_ok = False
                    hub_reason = "local-only: not published (no gh publisher / dev owner)"
                    report(f"{tag} · ✓ new local elite (not published to the hub)")
                elif is_set:
                    register_win_slices(hub, token=token, child_manifest=manifest,
                                        evidence=dev_ev, identities=identities,
                                        parent_digest=active.digest, reference=reference)
                else:
                    register_win(hub, token=token, child_manifest=manifest,
                                 evidence=dev_ev, parent_digest=active.digest,
                                 reference=reference)
            except AuthError as e:
                # The adapter's own error is owner-agnostic (it never knows
                # WHOSE run failed to auth) -- add that context here, once,
                # rather than in every caller of HubClient.register.
                hub_ok = False
                hub_reason = f"local-only: auth failed for run owner '{owner}' — {e}"
                report(f"{tag} · ⚠ win kept as a local elite; hub auth failed: {e}")
            except Exception as e:
                hub_ok = False
                hub_reason = f"local-only: hub error — {e}"
                report(f"{tag} · ⚠ win kept as a local elite; hub publish/register "
                       f"failed: {e}")
            # A rising union mean can still hide a per-identity drop on a set
            # objective -- diff the OLD parent (active, this island's champion
            # before the reassignment below) against the winning child so a
            # regression is surfaced, not silently absorbed into the
            # aggregate win (spec decision C §5/§9).
            regs = (aggregate.regressions(
                        aggregate.per_identity_means(active.dev_evidence.results),  # OLD parent
                        aggregate.per_identity_means(dev_ev.results))               # winning child
                    if identities else [])
            # Only THIS island's slot advances -- every other island's
            # champion (and its own recent_attempts) is untouched, which is
            # the state-isolation that makes islands a diversity mechanism.
            island_states[idx] = EliteState(
                digest, tree_store.path(digest), dev_fit, val_fit, dev_ev)
            wins += 1
            _emit("registered", k + 1, tokens=op.total,
                  detail=(f"⚠{len(regs)}" if regs else ""), hub_reason=hub_reason)
            if hub_ok:
                report(f"{tag} · ✓ REGISTERED dev={dev_fit:.3f} validation={val_fit:.3f}")
            _record(k + 1, IterationResult(True, "registered", dev_fitness=dev_fit,
                                           validation_fitness=val_fit, tokens=op.total,
                                           usage=op.usage, digest=digest,
                                           stopped_reason=op.stopped_reason,
                                           regressions=regs or None,
                                           causes=_causes(dev_ev.results),
                                           hub_reason=hub_reason))
        except Exception as e:
            _emit("error", k + 1, detail=str(e))
            report(f"{tag} · ✗ error: {e}")
            _record(k + 1, IterationResult(False, f"error:{e}"))
            continue
        finally:
            # `finally` (not a check placed after the try/except) so this
            # fires after EVERY iteration outcome -- win, reject, gate
            # failure, operator error -- since every one of those paths
            # above reaches this point via a `continue` (or falls through
            # after a win), and `continue` still runs an enclosing
            # `finally` before it actually advances the loop.
            if islands > 1 and (k + 1) % reset_period == 0:
                _reset_islands()
    _emit("done", iterations)
    return results
