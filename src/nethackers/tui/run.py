"""An app-owned evolution run: the background worker's accumulated monitor
state, kept whether or not a monitor is on screen so a ``RunMonitor`` can be
opened, left, and reopened. Pure data + the same worker->UI reductions the old
``EvolveScreen`` applied inline -- no Textual widgets, so it's unit-tested."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from statistics import pstdev

from nethackers.harness.aggregate import end_status_word
from nethackers.harness.loop import IterationResult
from nethackers.harness.metering import Meter, TokenUsage
from nethackers.tui.prettify import prettify
from nethackers.tui.status import EvolveConfig

_INITIAL_STATE: dict = {
    "phase": "cold-start", "iteration": 0, "baseline_dev": 0.0, "baseline_held": 0.0,
    "best_dev": 0.0, "best_held": 0.0, "wins": 0, "tokens": 0, "detail": "",
    "hub_reason": None,
    "parent_digest": "", "parent_dev": 0.0, "parent_held": 0.0, "generation": 0,
    "cell": None, "cells": [], "coverage": (0, 0),
}


@dataclass
class Batch:
    """One eval batch's episode rows, keyed by index (== batch/seed position)
    so parallel eval that finishes out of order still reads in batch order."""

    label: str
    rows_by_index: dict[int, dict] = field(default_factory=dict)
    done: bool = False

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


class Run:
    """One evolution's live state. ``apply_*`` are the worker->UI reductions
    (called on the UI thread, from the app's run worker); the read helpers
    feed a monitor's backfill and the Runs list."""

    def __init__(self, rid: str, cfg: EvolveConfig,
                 stop: threading.Event | None = None) -> None:
        self.rid = rid
        self.cfg = cfg
        self.status = "running"  # running | done | failed | stopped
        self.results: object | None = None
        self.error: BaseException | None = None
        self.stop = stop if stop is not None else threading.Event()
        self.started = time.monotonic()   # wall-clock start (for run_time)
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

    # ---- worker -> UI reductions (UI thread) --------------------------------
    def apply_state(self, state: dict) -> None:
        prev = self.state.get("phase")
        self.state = state
        if state.get("phase") == "cold-start":
            self.init_cells = {c["identity"]: c for c in (state.get("cells") or [])}
            self.init_union = state.get("union")
            self.init_cell_results = state.get("cell_results") or {}
        phase = state["phase"]
        if phase == "mutating":
            self.mut_start = time.monotonic()
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

    def apply_episode(self, label: str, ep: dict) -> None:
        batch = self.current_batch()
        if batch is None or batch.label != label:
            if batch is not None:
                batch.done = True
            batch = Batch(label=label)
            self.batches.append(batch)
            self.counts = {}
        batch.rows_by_index[int(ep["index"])] = ep
        self.counts[ep["status"]] = self.counts.get(ep["status"], 0) + 1
        rows = batch.rows()
        mean = sum(float(r["progress"]) for r in rows) / len(rows)
        total = int(ep["total"])
        # Each callback represents a finished episode. Seal the batch as soon
        # as every expected result has arrived; otherwise a completed batch
        # incorrectly keeps showing "running… n/n" throughout a following
        # non-evaluation phase such as mutation.
        batch.done = len(rows) >= total
        # done/total = how many of this batch's episodes have finished (a true
        # completed-count), not the arriving episode's own (out-of-order) index.
        self.eval_step = (len(rows), total, mean)

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
        self.finished_at = time.monotonic()
        batch = self.current_batch()
        if batch is not None:
            batch.done = True
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
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started

    def elapsed(self) -> float:
        if self.state.get("phase") == "mutating":
            return time.monotonic() - self.mut_start
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
        """The set objective's identities, or [] -- the loop only puts
        "identities" in state for set objectives; single/random runs never
        set it, so this stays empty for them."""
        return list(self.state.get("identities") or [])

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
        if ident in cells and self.origins().get(cells[ident]["digest"], {}).get("kind") == "hub":
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
