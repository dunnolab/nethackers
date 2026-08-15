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
    "parent_digest": "", "parent_dev": 0.0, "parent_held": 0.0, "generation": 0,
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

        self.state: dict = dict(_INITIAL_STATE)
        self.chain: list[str] = []
        self.ledger_rows: list[tuple[int, bool, str]] = []
        self.counts: dict[str, int] = {}
        self.batches: list[Batch] = []
        self.logs: dict[str, list[tuple[str, str]]] = {}
        self.meters: dict[str, Meter] = {}
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
        pd = state.get("parent_digest")
        if pd and (not self.chain or self.chain[-1] != pd):
            self.chain.append(pd)  # seed -> elite1 -> elite2 ...
        if phase == "registered":
            self.ledger_rows.append((state["iteration"], True, "registered"))
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
        # done/total = how many of this batch's episodes have finished (a true
        # completed-count), not the arriving episode's own (out-of-order) index.
        self.eval_step = (len(rows), int(ep["total"]), mean)

    def apply_log(self, tag: str, line: str) -> None:
        self.logs.setdefault(tag, []).extend(prettify(self.cfg.backend, line))
        self.meters.setdefault(tag, Meter(self.cfg.backend)).observe(line)

    def finish(self, *, results: object | None = None,
               error: BaseException | None = None) -> None:
        self.results = results
        self.error = error
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
        return meter.usage.total if meter is not None else 0

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
