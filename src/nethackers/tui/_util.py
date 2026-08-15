"""Shared helpers used by tui screens. Copied (not imported) from
tui.app.EvolveApp's module-level definitions per Ruling R-util: app.py keeps
its own copies for now (it is frozen pending its Task 10 rework onto
NetHackersApp), and screens must not import from tui.app -- a later task
makes app.py import the screens defined here, so `tui.app -> tui._util`
would become a cycle if screens imported the other direction."""
from __future__ import annotations

import functools


def _slug(tag: str) -> str:
    return "log_" + tag.replace(" ", "_").replace("/", "_")


def _rows_in_order(rows_by_index: dict[int, dict]) -> list[dict]:
    """Render a batch's episode rows in index (== batch/seed) order,
    regardless of the order they actually completed in -- parallel eval
    (M3) means episode k+1 can finish before episode k."""
    return [rows_by_index[k] for k in sorted(rows_by_index)]


def _guarded(method):
    """Per the M3 evolve TUI design spec's Errors section: "a callback
    raising is caught and dropped (logged to a debug buffer, never
    surfaced)". Without this, an exception raised inside a handler body
    propagates through call_from_thread's future.result() back into the
    worker thread -- aborting the whole evolve run over a display concern
    (malformed episode/state dict, bad log line), including at the
    cold-start/done call sites in run_loop that sit outside its
    per-iteration try/except."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self.log.error(f"display handler {method.__name__} raised: {exc!r}; dropped")
            return None
    return wrapper
