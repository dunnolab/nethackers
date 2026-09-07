"""Screen-level tests for the reworked RunMonitor: iteration list + Progress/
Mutator Logs/Logs tabs + a clickable Progress table, rendered from a ``Run``.
(The worker->Run reductions themselves are covered by test_tui_run.py.)"""
from __future__ import annotations

from textual.app import App
from textual.widgets import DataTable

from nethackers.tui.run import Run
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig("wiz-elf-cha-mal,wiz-orc-cha-mal,val-dwa-law-fem", "claude", 3,
                   model="opus", effort="high", operator_version="1.2.7")


def _run() -> Run:
    r = Run("r1", CFG)
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal", "val-dwa-law-fem"]
    r.apply_state({"phase": "cold-start", "iteration": 0, "identities": ids,
                   "cells": [{"identity": i, "score": 0.4, "digest": f"d:{i}"} for i in ids],
                   "origins": {f"d:{i}": {"kind": "hub", "handle": "clyde", "sha": "11",
                                          "repo": "github.com/t/a", "iteration": None}
                               for i in ids},
                   "aa_baseline": {i: 0.3 for i in ids},
                   "union": {"score": 0.48, "digest": "u1"},
                   "cell_results": {i: [{"character": i, "trajectory_id": 0, "progress": 0.4,
                                         "status": "completed", "end_status": "died",
                                         "cause_of_death": "killed by a newt", "max_depth": 6,
                                         "turns": 900, "wall_seconds": 12.0}] for i in ids},
                   "coverage": (3, 3), "cell": None, "generation": 0,
                   "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0, "detail": "",
                   "parent_digest": "", "parent_dev": 0.0})
    return r


class _Host(App):
    def __init__(self, run):
        super().__init__()
        self._run = run

    def on_mount(self):
        self.push_screen(RunMonitor(self._run))


def _dump(dt: DataTable) -> str:
    """All of a DataTable's cell text, newline-joined.

    NOT ``str(dt.render())``: ``DataTable`` never overrides ``Widget.render()``
    (it paints via its own ``render_line``/``render_lines``), so calling
    ``.render()`` directly falls through to ``ScrollView.render()`` -- a debug
    placeholder returning ``Panel(f"{scroll_offset} {show_vertical_scrollbar}")``
    -- which never contains cell text regardless of the table's contents
    (verified empirically against installed Textual 8.2.8). Walking the actual
    stored cell values via ``get_row_at`` is the real content instead; ``str()``
    on each ``Text``/``str`` cell value strips markup to plain text, exactly
    like ``str(some_static.render())`` does for a ``Static``.
    """
    return "\n".join(" | ".join(str(v) for v in dt.get_row_at(r))
                      for r in range(dt.row_count))


async def test_progress_table_has_best_overall_and_role_groups():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        assert isinstance(mon, RunMonitor)
        table_text = _dump(mon.query_one("#idents", DataTable))
        assert "BEST OVERALL" in table_text
        assert "Wizard" in table_text and "Valkyrie" in table_text   # role groups present
        assert "Claude Code" in str(mon.query_one("#title_mutator").render())


async def test_init_row_shows_no_mutation():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon._select(0)
        await pilot.pause()
        assert "no mutation" in _dump(mon.query_one("#idents", DataTable))
