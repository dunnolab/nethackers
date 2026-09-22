"""An app-owned evolution run: the background worker's accumulated monitor
state, kept whether or not a monitor is on screen so a ``RunMonitor`` can be
opened, left, and reopened. Pure data + the same worker->UI reductions the old
``EvolveScreen`` applied inline -- no Textual widgets, so it's unit-tested."""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import pstdev

from nethackers.harness.aggregate import end_status_word
from nethackers.harness.loop import IterationResult
from nethackers.harness.metering import Meter, TokenUsage
from nethackers.harness.seeds import dev_spec
from nethackers.hub.selector import resolve
from nethackers.tui.prettify import prettify
from nethackers.tui.status import EvolveConfig

_INITIAL_STATE: dict = {
    "phase": "cold-start", "iteration": 0, "baseline_dev": 0.0, "baseline_held": 0.0,
    "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
    "hub_reason": None,
    "parent_digest": "", "parent_dev": 0.0, "parent_held": 0.0, "generation": 0,
    "cell": None, "cells": [], "coverage": (0, 0),
}

# harness/loop.py labels an iteration's dev eval `iter <k>/<n> · dev`
_DEV_LABEL = re.compile(r"^iter (\d+)/\d+ · dev$")


@dataclass
class Batch:
    """One eval batch's episode rows, keyed by index (== batch/seed position)
    so parallel eval that finishes out of order still reads in batch order."""

    label: str
    rows_by_index: dict[int, dict] = field(default_factory=dict)
    done: bool = False
    ended: float | None = None   # when the last expected episode arrived (run clock)
    total: int = 0               # episodes this batch plays (from the stream)

    def rows(self) -> list[dict]:
        return [self.rows_by_index[k] for k in sorted(self.rows_by_index)]


@dataclass
class EvalView:
    ident: str
    total: int
    rows: list[dict]                 # normalized seed rows (see _seed_row)

    @property
    def revealed(self) -> int:
        return len(self.rows)

    @property
    def done(self) -> bool:
        return self.total > 0 and self.revealed >= self.total

    @property
    def scores(self) -> list[float]:
        return [float(r["progress"]) for r in self.rows]

    @property
    def avg(self) -> float | None:
        s = self.scores
        return sum(s) / len(s) if s else None

    @property
    def std(self) -> float:
        s = self.scores
        return pstdev(s) if len(s) > 1 else 0.0


def _status_word(row: dict) -> str:
    """Normalize a seed row's status to a word: ascended | died | aborted |
    timed out (| running). Completed episodes carry the raw NLE ``end_status``
    code, translated via ``end_status_word`` (shared with the mutator brief);
    live rows (parsed from the arena's stderr, which omits end_status) fall back
    to a best guess from the arena ResultStatus."""
    if row.get("ascended"):
        return "ascended"
    st = str(row.get("status") or "")
    if "timeout" in st or st == "timed out":
        return "timed out"
    word = end_status_word(row.get("end_status"))
    if word is not None:
        return word
    return "died" if st in ("", "completed") else st


def _seed_row(raw: dict) -> dict:
    """Normalize either a live episode dict {seed,progress,status,turns,depth}
    or a completed TrajectoryResult.to_dict() into the detail view's row shape.
    Live rows have no cause/time yet -> None (rendered as '—')."""
    seed = raw.get("seed", raw.get("trajectory_id"))
    depth = raw.get("depth", raw.get("max_depth"))
    return {
        "seed": seed,
        "progress": float(raw.get("progress", 0.0)),
        "status": _status_word(raw),
        "cause": raw.get("cause_of_death"),           # None for live / non-death
        "depth": depth,
        "turns": raw.get("turns"),
        "time": raw.get("wall_seconds"),              # None for live
    }


@dataclass
class IterTimes:
    """When one iteration reached each step, on the run's clock; None until then."""

    edit_start: float | None = None
    edit_end: float | None = None
    smoke_end: float | None = None
    play_end: float | None = None
    decided: float | None = None


def outcome_word(result: IterationResult) -> str:
    """The plain word for a decided iteration -- improved | no gain |
    failed test | agent failed | error -- from the loop's reason. Never
    "kept"/"discarded": the loop sends every scored bot to the hub."""
    if result.registered:
        return "improved"
    reason = result.reason or ""
    if reason.startswith("gate:"):
        return "failed test"
    if reason.startswith("operator-error:"):
        return "agent failed"
    if reason.startswith("error:"):
        return "error"
    return "no gain"


class Run:
    """One evolution's live state. ``apply_*`` are the worker->UI reductions
    (called on the UI thread, from the app's run worker); the read helpers
    feed a monitor's backfill and the Runs list."""

    def __init__(self, rid: str, cfg: EvolveConfig, stop: threading.Event | None = None,
                 *, clock: Callable[[], float] | None = None) -> None:
        self.rid = rid
        self.cfg = cfg
        self.status = "running"  # running | done | failed | stopped
        # True for a run rebuilt from disk (an earlier session's record) rather
        # than driven live: its durable log has the iterations/outcomes/scores
        # and the mutator transcript, but the per-seed detail and per-identity
        # Progress scores were never persisted -- the monitor shows them as
        # "not recorded" instead of the misleading AutoAscend fallback.
        self.reopened = False
        self.results: object | None = None
        self.error: BaseException | None = None
        self.stop = stop if stop is not None else threading.Event()
        self._clock = clock or time.monotonic
        self.started = self._clock()   # wall-clock start (for run_time)
        self.finished_at: float | None = None

        self.state: dict = dict(_INITIAL_STATE)
        # The cold-start archive/union, frozen as of the LAST cold-start-phase
        # emit -- unlike self.state["cells"]/["union"], which are overwritten
        # to the latest live archive on every subsequent emit. incumbent()/
        # best_overall() need this frozen starting point so a past-iteration
        # view still shows the champion that was actually incumbent THEN, not
        # whatever cell a later run child has since taken.
        self.init_cells: dict[str, dict] = {}
        self.init_union: dict | None = None
        self.init_cell_results: dict[str, list[dict]] = {}
        self.chain: list[str] = []
        self.ledger_rows: list[tuple[int, bool, str]] = []
        self.counts: dict[str, int] = {}
        self.batches: list[Batch] = []
        self.logs: dict[str, list[tuple[str, str]]] = {}
        self.meters: dict[str, Meter] = {}
        self.iter_results: dict[int, IterationResult] = {}   # iteration -> result
        # iteration -> {"target": state["cell"], "seed_desc": ...}, recorded at
        # the "mutating" state so the monitor's ✎ marker + process log can name
        # which cell/seed a not-yet-decided iteration is mutating.
        self.iter_meta: dict[int, dict] = {}
        self.sel_tag: str | None = None
        self.eval_step: tuple[int, int, float] | None = None
        self.mut_start = 0.0

        # The story's timeline, on the run's clock (see tui/story.py).
        self.first_state_at: float | None = None      # the pre-state hub fetch ended
        self.setup_ended_at: float | None = None      # the phase first left cold-start
        self.stop_requested_at: float | None = None   # Stop was pressed
        self.iter_times: dict[int, IterTimes] = {}
        self._objective_ids: list[str] | None = None
        self._games_total: int | None = None

    # ---- worker -> UI reductions (UI thread) --------------------------------
    def apply_state(self, state: dict) -> None:
        prev = self.state.get("phase")
        now = self._clock()
        if self.first_state_at is None:
            self.first_state_at = now
        if (prev == "cold-start" and state["phase"] != "cold-start"
                and self.setup_ended_at is None):
            self.setup_ended_at = now
        self._stamp(state["phase"], int(state.get("iteration") or 0), now)
        self.state = state
        if state.get("phase") == "cold-start":
            self.init_cells = {c["identity"]: c for c in (state.get("cells") or [])}
            self.init_union = state.get("union")
            self.init_cell_results = state.get("cell_results") or {}
        phase = state["phase"]
        if phase == "mutating":
            self.mut_start = self._clock()
            tag = self.tag(state["iteration"])
            self.logs.setdefault(tag, [])
            self.sel_tag = tag
            self.iter_meta[state["iteration"]] = {
                "target": state.get("cell"),
                "seed_desc": ("best overall cell (union)" if state.get("cell") == "union"
                              else f"{state.get('cell')} cell elite")}
        elif phase in ("evaluating-dev", "evaluating-held") and prev != phase:
            self.eval_step = None
        # The single-lineage chain is only meaningful for a single-identity
        # run; a set (len(identities) > 1) has the loop pick a random cell
        # each iteration, so appending would splice unrelated cells' parents
        # into one fake chain. The cell-archive panel replaces the lineage
        # strip there.
        idents = state.get("identities") or []
        pd = state.get("parent_digest")
        if len(idents) <= 1 and pd and (not self.chain or self.chain[-1] != pd):
            self.chain.append(pd)  # seed -> elite1 -> elite2 ...
        if phase == "registered":
            # hub_reason (harness.loop's win-path) means the win never
            # reached the hub -- show WHY instead of the plain "registered",
            # which would otherwise silently overstate what happened. The
            # regression-count marker (`detail`) still appends the same way
            # either way, since it's an orthogonal signal (a set win can be
            # both local-only AND carry a per-identity regression).
            base = state.get("hub_reason") or "registered"
            reason = base + (f" {state['detail']}" if state.get("detail") else "")
            self.ledger_rows.append((state["iteration"], True, reason))
        elif phase == "rejected":
            self.ledger_rows.append(
                (state["iteration"], False, state["detail"] or "rejected"))

    def _stamp(self, phase: str, k: int, now: float) -> None:
        """Record when iteration k reached each step, from the loop's phases:
        mutating -> gating (edit done) -> evaluating-dev (smoke test passed) ->
        registered/rejected (decided); a gate rejection ends at the smoke
        test. "error"/"aborted" can land at ANY point -- the loop's outer
        `except` covers everything from `copytree` (before "mutating") through
        the gate and the dev eval -- so they stamp ONLY `decided`, never
        `edit_end`/`smoke_end`/a not-yet-set `edit_start`: a step is claimed
        done only when its OWN timestamp was actually reached (tui/story.py
        reads these to avoid claiming a step that never happened)."""
        if k <= 0:
            return
        if phase == "mutating":
            self.iter_times[k] = IterTimes(edit_start=now)
            return
        if phase not in ("gating", "evaluating-dev", "registered", "rejected",
                         "error", "aborted"):
            return
        t = self.iter_times.setdefault(k, IterTimes())
        if phase in ("error", "aborted"):
            if t.decided is None:
                t.decided = now
            return
        if t.edit_start is None:
            t.edit_start = now     # a state that skipped "mutating" still started it
        if t.edit_end is None:
            t.edit_end = now
        if phase in ("evaluating-dev", "registered", "rejected") and t.smoke_end is None:
            t.smoke_end = now
        if phase in ("registered", "rejected") and t.decided is None:
            t.decided = now

    def apply_episode(self, label: str, ep: dict) -> None:
        now = self._clock()
        batch = self.current_batch()
        if batch is None or batch.label != label:
            if batch is not None:
                self._seal(batch, now)
            batch = Batch(label=label)
            self.batches.append(batch)
            self.counts = {}
        batch.rows_by_index[int(ep["index"])] = ep
        self.counts[ep["status"]] = self.counts.get(ep["status"], 0) + 1
        rows = batch.rows()
        mean = sum(float(r["progress"]) for r in rows) / len(rows)
        total = int(ep["total"])
        batch.total = total
        # Each callback represents a finished episode. Seal the batch as soon
        # as every expected result has arrived; otherwise a completed batch
        # incorrectly keeps showing "running… n/n" throughout a following
        # non-evaluation phase such as mutation.
        if len(rows) >= total:
            self._seal(batch, now)
        # done/total = how many of this batch's episodes have finished (a true
        # completed-count), not the arriving episode's own (out-of-order) index.
        self.eval_step = (len(rows), total, mean)

    def _seal(self, batch: Batch, now: float) -> None:
        """Mark a batch finished, once: when -- and, for an iteration's dev
        batch, when that iteration's games ended."""
        if batch.ended is not None:
            return
        batch.done = True
        batch.ended = now
        m = _DEV_LABEL.match(batch.label)
        if m:
            t = self.iter_times.setdefault(int(m.group(1)), IterTimes())
            if t.play_end is None:
                t.play_end = now

    def apply_log(self, tag: str, line: str) -> None:
        self.logs.setdefault(tag, []).extend(prettify(self.cfg.backend, line))
        self.meters.setdefault(tag, Meter(self.cfg.backend)).observe(line)

    def apply_iteration(self, iteration: int, result: IterationResult) -> None:
        """Fold one completed iteration's IterationResult (harness/loop.py) into
        the run: registered/rejected, which cells improved (incl. "union"),
        per-kind usage, causes, and per-seed results. Delivered by the worker's
        on_iteration callback (launch.py), in addition to metrics.jsonl."""
        self.iter_results[iteration] = result

    def finish(self, *, results: object | None = None,
               error: BaseException | None = None) -> None:
        self.results = results
        self.error = error
        self.finished_at = self._clock()
        batch = self.current_batch()
        if batch is not None:
            self._seal(batch, self.finished_at)
        if error is not None:
            self.status = "failed"
        elif self.stop.is_set():
            self.status = "stopped"
        else:
            self.status = "done"

    @property
    def running(self) -> bool:
        return self.status == "running"

    # ---- read helpers -------------------------------------------------------
    def tag(self, iteration: int) -> str:
        return f"iter {iteration}/{self.cfg.iterations}"

    def dev_label(self, k: int) -> str:
        """The loop's stream label for iteration k's dev-eval batch
        (harness/loop.py's ``f"{tag} · dev"``) -- the one place that knows
        it, so callers (tui/story.py) never repeat the pattern by hand and
        risk matching the smoke batch (``f"{tag} · smoke"``) instead."""
        return f"{self.tag(k)} · dev"

    def current_batch(self) -> Batch | None:
        return self.batches[-1] if self.batches else None

    def running_tag(self) -> str:
        if self.state.get("phase") == "mutating":
            return self.tag(self.state["iteration"])
        return ""

    def live_tokens(self) -> int:
        meter = self.meters.get(self.running_tag())
        return meter.usage.spend if meter is not None else 0

    def total_tokens(self) -> int:
        """Cumulative real spend across every iteration (for the Runs list;
        ``live_tokens`` is just the current iteration's). Excludes cheap cache
        reads -- see ``TokenUsage.spend``."""
        return sum(meter.usage.spend for meter in self.meters.values())

    def run_time(self) -> float:
        """Wall-clock seconds since the run started (frozen once finished)."""
        end = self.finished_at if self.finished_at is not None else self._clock()
        return end - self.started

    def elapsed(self) -> float:
        if self.state.get("phase") == "mutating":
            return self._clock() - self.mut_start
        return 0.0

    def split(self) -> str:
        """'dev'/'held' from the phase, falling back to the current batch
        label for phases (mutating/gating/...) that don't name a split."""
        phase = str(self.state.get("phase", ""))
        if phase.endswith("dev"):
            return "dev"
        if phase.endswith("held"):
            return "held"
        batch = self.current_batch()
        return "held" if (batch and "held" in batch.label) else "dev"

    def identities(self) -> list[str]:
        """The objective's identities: the loop's own list once a state names
        them, else resolved from the objective itself -- so the monitor has its
        rows before the hub fetch finishes. [] for an objective that doesn't
        resolve (e.g. a test's made-up identities)."""
        named = self.state.get("identities")
        if named:
            return list(named)
        if self._objective_ids is None:
            try:
                self._objective_ids = sorted(resolve(self.cfg.objective).identities)
            except ValueError:
                self._objective_ids = []
        return list(self._objective_ids)

    def cells(self) -> list[dict]:
        """The MAP-Elites cell archive as of the last state: one
        {identity, score, digest} row per identity currently filled."""
        return list(self.state.get("cells") or [])

    def coverage(self) -> tuple[int, int]:
        """(filled, total) cells in the archive."""
        cov = self.state.get("coverage") or (0, 0)
        return (int(cov[0]), int(cov[1]))

    def origins(self) -> dict[str, dict]:
        return dict(self.state.get("origins") or {})

    def aa_baseline(self) -> dict[str, float]:
        return dict(self.state.get("aa_baseline") or {})

    def elite_of(self) -> dict[str, dict]:
        """identity -> its pulled hub champion {program_id, score}, emitted from
        cold-start onward so ``incumbent`` can show the champion's label + score
        while its local eval is still streaming (before its cell is scored)."""
        return dict(self.state.get("elite_of") or {})

    def active_cell(self) -> str | None:
        """The identity of the cell the current iteration is mutating, or
        None (cold-start / done)."""
        return self.state.get("cell")

    def parent_means(self) -> dict[str, float]:
        """The parent's per-identity means, or {} when absent (single/random
        objectives, or before the first mutating state)."""
        return dict(self.state.get("parent_means") or {})

    def candidate_means(self) -> dict[str, float]:
        """Per-identity means of the current dev batch's rows, grouped by
        each episode's ``character``. {} before any batch exists."""
        batch = self.current_batch()
        if batch is None:
            return {}
        buckets: dict[str, list[float]] = {}
        for row in batch.rows():
            c = row.get("character")
            if c:
                buckets.setdefault(c, []).append(float(row["progress"]))
        return {c: sum(v) / len(v) for c, v in buckets.items()}

    def role_of(self, ident: str) -> str:
        return ident.split("-", 1)[0]

    def roles_present(self) -> list[str]:
        out: list[str] = []
        for i in self.identities():
            role = self.role_of(i)
            if role not in out:
                out.append(role)
        return out

    def token_usage(self) -> TokenUsage:
        total = TokenUsage()
        for meter in self.meters.values():
            total = total + meter.usage
        return total

    def _origin_label(self, digest: str) -> tuple[str, str]:
        """(label, kind) for a program digest from the origins map."""
        o = self.origins().get(digest)
        if o is None:
            return "seed", "aa"
        if o["kind"] == "hub":
            return f"{o.get('handle') or '?'} @{o.get('sha') or '?'}", "hub"
        if o["kind"] == "run":
            return f"run · iter {o.get('iteration')}", "run"
        return "AutoAscend", "aa"

    def _completed_iters(self, upto_k: int) -> list[tuple[int, IterationResult]]:
        """(k, IterationResult) for finished iterations strictly before upto_k
        that carry per-seed results, in order."""
        return [(k, self.iter_results[k]) for k in sorted(self.iter_results)
                if 0 < k < upto_k and self.iter_results[k].results is not None]

    def incumbent(self, ident: str, upto_k: int) -> tuple[float, str, str, int | None]:
        cells = self.init_cells
        elite = self.elite_of().get(ident)
        if ident in cells:
            # A cold-start cell's measured score is the best so far
            # regardless of its origin's kind -- a hub champion AND a seed
            # cell (--from-seed/--seed, no hub champion at all) are both
            # real, played scores; only an identity with NO cell yet falls
            # back to the AutoAscend floor below. `_origin_label` gives the
            # right label either way.
            label, kind = self._origin_label(cells[ident]["digest"])
            score, j = float(cells[ident]["score"]), None
        elif elite is not None and self.state.get("phase") == "cold-start":
            # Cold-start: this identity's hub champion is pulled but its local
            # eval hasn't scored into a cell yet -- show the champion's label +
            # its LIVE (partial) local mean, or the hub-reported score until the
            # first episode lands, instead of falsely showing AutoAscend.
            label, kind = self._origin_label(elite["program_id"])
            live = self.candidate_means().get(ident)
            score = live if live is not None else float(elite.get("score", 0.0))
            j = None
        else:
            score, label, kind, j = self.aa_baseline().get(ident, 0.0), "AutoAscend", "aa", None
        for k, res in self._completed_iters(upto_k):
            vals = [float(r["progress"]) for r in (res.results or [])
                    if r.get("character") == ident]
            if vals:
                avg = sum(vals) / len(vals)
                if avg > score:
                    score, label, kind, j = avg, f"run · iter {k}", "run", k
        return score, label, kind, j

    def best_overall(self, upto_k: int) -> tuple[float, str, str, int | None]:
        union = self.init_union
        if union is not None:
            label, kind = self._origin_label(union["digest"])
            score, j = float(union["score"]), None
        else:   # AutoAscend fallback: macro-average of the baselines (spec §5.6)
            floors = [self.aa_baseline().get(i, 0.0) for i in self.identities()]
            score = sum(floors) / len(floors) if floors else 0.0
            label, kind, j = "AutoAscend", "aa", None
        for k, res in self._completed_iters(upto_k):
            if (res.improved and "union" in res.improved and res.dev_fitness is not None
                    and float(res.dev_fitness) > score):
                score, label, kind, j = float(res.dev_fitness), f"run · iter {k}", "run", k
        return score, label, kind, j

    def _batch_rows_for(self) -> dict[str, list[dict]]:
        """The current live dev batch's per-identity rows, grouped by character
        (the running iteration's stream). {} when no batch exists yet."""
        batch = self.current_batch()
        if batch is None:
            return {}
        out: dict[str, list[dict]] = {}
        for row in batch.rows():
            c = row.get("character")
            if c:
                out.setdefault(c, []).append(_seed_row(row))
        return out

    def iteration_evals(self, k: int) -> dict[str, EvalView]:
        idents = self.identities()
        total = self._per_ident_total()
        if k == 0:
            # cold-start: scored cells from the snapshot; for an identity whose
            # champion is still streaming (not scored into a cell yet) fall back
            # to the LIVE batch, so its detail shows the in-flight episodes
            # rather than an empty table.
            live = self._batch_rows_for() if self.state.get("phase") == "cold-start" else {}
            out: dict[str, EvalView] = {}
            for i in idents:
                scored = [r for r in self.init_cell_results.get(i, [])
                          if r.get("character") in (i, None)]
                out[i] = (EvalView(i, total, [_seed_row(r) for r in scored]) if scored
                          else EvalView(i, total, live.get(i, [])))
            return out
        if k in self.iter_results and self.iter_results[k].results is not None:
            src: dict[str, list[dict]] = {i: [] for i in idents}
            for r in self.iter_results[k].results or []:
                c = r.get("character")
                if c:
                    src.setdefault(c, []).append(r)
            return {i: EvalView(i, total, [_seed_row(r) for r in src.get(i, [])])
                    for i in idents}
        # Only the actually-running iteration streams live per-seed rows. A
        # completed-but-no-eval iteration (gate/error reject, results=None) or a
        # not-yet-started one has none -> empty (not another iteration's batch).
        if self.iteration_status(k) == "running":
            live2 = self._batch_rows_for()
            return {i: EvalView(i, total, live2.get(i, [])) for i in idents}
        return {i: EvalView(i, total, []) for i in idents}

    def union_evals(self) -> dict[str, EvalView]:
        """Per-identity EvalViews for the BEST OVERALL (union) HUB champion's
        OWN cold-start union eval (delivered in init_union['results']),
        grouped by character. {} when no snapshot / no results."""
        rows_by: dict[str, list[dict]] = {}
        for r in (self.init_union or {}).get("results") or []:
            c = r.get("character")
            if c is not None:
                rows_by.setdefault(c, []).append(r)
        total = self._per_ident_total()
        return {i: EvalView(i, total, [_seed_row(r) for r in rows_by.get(i, [])])
                for i in self.identities()}

    def _per_ident_total(self) -> int:
        """Best-effort per-identity seed count for progress ratios (seeds/ident)."""
        exact = self.games_per_identity()
        if exact:
            return exact
        cr = self.init_cell_results or {}
        # per-identity episode counts from the cold-start snapshot; use them only
        # if ANY is non-zero (a dict of empty lists -- early cold-start, before
        # a champion has scored -- must fall through to the live batch, else the
        # ratio shows "n/0").
        counts = [sum(1 for r in v if r.get("character") in (i, None)) for i, v in cr.items()]
        if any(counts):
            return max(counts)
        batch = self.current_batch()
        if batch and batch.rows() and self.identities():
            return max(1, int(batch.rows()[0].get("total", 0)) // len(self.identities()))
        return 0

    def iteration_status(self, k: int) -> str:
        if k == 0:
            return "init"
        res = self.iter_results.get(k)
        if res is not None:
            return "registered" if res.registered else "rejected"
        if self.state.get("iteration") == k and self.running:
            return "running"
        return "pending"

    def iter_target(self, k: int) -> str | None:
        """The cell/identity iteration ``k`` mutates (or "union"), from the
        "mutating" state recorded into ``iter_meta`` -- None before that
        iteration has started."""
        return (self.iter_meta.get(k) or {}).get("target")

    # ---- the story's data (tui/story.py) --------------------------------------
    def now(self) -> float:
        """The run's clock: monotonic seconds, injectable for tests."""
        return self._clock()

    def request_stop(self) -> None:
        """Stop the run, remembering when (the story words a stop by it)."""
        if self.stop_requested_at is None:
            self.stop_requested_at = self._clock()
        self.stop.set()

    def batch_for(self, label: str) -> Batch | None:
        """The most recent batch the loop streamed under ``label``."""
        return next((b for b in reversed(self.batches) if b.label == label), None)

    def origin_label(self, digest: str) -> str:
        """A program's display label: ``clyde @a1b2c3d``, ``run · iter 3``."""
        return self._origin_label(digest)[0]

    def games_total(self) -> int:
        """Games in one full evaluation (every identity's seeds), from the
        harness's own dev spec; 0 if the objective doesn't resolve."""
        if self._games_total is None:
            try:
                self._games_total = len(dev_spec(self.cfg.objective).batch)
            except (ValueError, KeyError):
                self._games_total = 0
        return self._games_total

    def games_per_identity(self) -> int:
        n = len(self.identities())
        return self.games_total() // n if n else 0

    def actions(self, k: int) -> list[str]:
        """Iteration k's agent actions -- its prettified "tool" lines, which
        every operator streams live (unlike token usage: codex reports that
        once, when the edit ends)."""
        return [text for kind, text in self.logs.get(self.tag(k), []) if kind == "tool"]

    def edit_usage(self, k: int) -> TokenUsage:
        meter = self.meters.get(self.tag(k))
        return meter.usage if meter is not None else TokenUsage()

    def edit_finished(self, k: int) -> bool:
        t = self.iter_times.get(k)
        return (t is not None and t.edit_end is not None) or k in self.iter_results

    def finished_usage(self) -> TokenUsage:
        """Tokens of FINISHED edits only -- the same meaning for every agent,
        since codex reports usage only when an edit ends."""
        total = TokenUsage()
        for k in range(1, self.cfg.iterations + 1):
            if self.edit_finished(k):
                total = total + self.edit_usage(k)
        return total

    def iteration_duration(self, k: int) -> float | None:
        t = self.iter_times.get(k)
        if t is None or t.edit_start is None or t.decided is None:
            return None
        return t.decided - t.edit_start

    def running_iteration(self) -> int | None:
        """The iteration in progress (started, not decided), or None."""
        if not self.running:
            return None
        for k in sorted(self.iter_times):
            t = self.iter_times[k]
            if t.edit_start is not None and t.decided is None:
                return k
        return None

    def setup_duration(self) -> float | None:
        return None if self.setup_ended_at is None else self.setup_ended_at - self.started

    def pace_left(self, now: float | None = None) -> float | None:
        """'At this pace': the mean time of this run's finished iterations x the
        iterations after the current one, plus what's left of the mean for the
        current one (never negative: an overrun doesn't eat into later
        iterations). None until one iteration has finished, while stopping, and
        for runs reopened from disk (no timings)."""
        if self.reopened or not self.running or self.stop.is_set():
            return None
        durations = [d for k in sorted(self.iter_times)
                     if (d := self.iteration_duration(k)) is not None]
        if not durations:
            return None
        now = self._clock() if now is None else now
        per = sum(durations) / len(durations)
        current = self.running_iteration()
        after = self.cfg.iterations - len(durations) - (1 if current is not None else 0)
        left = per * max(0, after)
        if current is not None:
            started = self.iter_times[current].edit_start
            if started is not None:
                left += max(0.0, per - (now - started))
        return left
