"""An app-owned evolution run: the background worker's accumulated monitor
state, kept whether or not a monitor is on screen so a ``RunMonitor`` can be
opened, left, and reopened. Pure data + the same worker->UI reductions the old
``EvolveScreen`` applied inline -- no Textual widgets, so it's unit-tested."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from nethackers.harness.metering import Meter
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
        self.chain: list[str] = []
        self.ledger_rows: list[tuple[int, bool, str]] = []
        self.counts: dict[str, int] = {}
        self.batches: list[Batch] = []
        self.logs: dict[str, list[tuple[str, str]]] = {}
        self.meters: dict[str, Meter] = {}
        self.iter_results: dict[int, object] = {}   # iteration -> IterationResult
        self.sel_tag: str | None = None
        self.eval_step: tuple[int, int, float] | None = None
        self.mut_start = 0.0

    # ---- worker -> UI reductions (UI thread) --------------------------------
    def apply_state(self, state: dict) -> None:
        prev = self.state.get("phase")
        self.state = state
        phase = state["phase"]
        if phase == "mutating":
            self.mut_start = time.monotonic()
            tag = self.tag(state["iteration"])
            self.logs.setdefault(tag, [])
            self.sel_tag = tag
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

    def apply_iteration(self, iteration: int, result: object) -> None:
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
