"""Screen-level tests for the reworked RunMonitor: iteration list + Progress/
Mutator Logs/Logs tabs + a clickable Progress table, rendered from a ``Run``.
(The worker->Run reductions themselves are covered by test_tui_run.py.)"""
from __future__ import annotations

from textual.app import App
from textual.widgets import DataTable

from nethackers.harness.loop import IterationResult
from nethackers.tui.run import Run
from nethackers.tui.screens.monitor import RunMonitor
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig("wiz-elf-cha-mal,wiz-orc-cha-mal,val-dwa-law-fem", "claude", 3,
                   model="opus", effort="high", operator_version="1.2.7")


def _run() -> Run:
    r = Run("r1", CFG)
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal", "val-dwa-law-fem"]
    # "u1" (the union seed) is a DIFFERENT hub champion than the per-identity
    # elites (its own handle/sha/repo) -- matches real cold-start, where the
    # union cell is seeded from the board's overall champion, not necessarily
    # any one identity's own champion (harness/loop.py select.overall_champion).
    origins = {f"d:{i}": {"kind": "hub", "handle": "clyde", "sha": "11",
                          "repo": "github.com/t/a", "iteration": None}
               for i in ids}
    origins["u1"] = {"kind": "hub", "handle": "clyde", "sha": "33",
                      "repo": "github.com/t/u", "iteration": None}
    r.apply_state({"phase": "cold-start", "iteration": 0, "identities": ids,
                   "cells": [{"identity": i, "score": 0.4, "digest": f"d:{i}"} for i in ids],
                   "origins": origins,
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


def _headers(dt: DataTable) -> str:
    """A DataTable's column HEADER text, space-joined -- ``_dump`` only covers
    row values, but a header like "cause of death" never appears as cell
    content, so an assertion looking for it must read the columns instead."""
    return " | ".join(str(c.label) for c in dt.ordered_columns)


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


async def test_open_best_so_far_shows_per_seed_table_with_cause():
    r = _run()
    r.apply_iteration(1, IterationResult(True, "registered", improved=["wiz-elf-cha-mal"],
        dev_fitness=0.5, results=[{"character": "wiz-elf-cha-mal", "trajectory_id": 0,
            "progress": 0.5, "status": "completed", "end_status": "died", "ascended": False,
            "cause_of_death": "starvation", "max_depth": 8, "turns": 1200, "wall_seconds": 20.0}]))
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon._select(2)   # viewing iteration 2 -> incumbent propagated to iter 1
        mon.open_best("wiz-elf-cha-mal")
        await pilot.pause()
        assert mon.detail_open is True
        # str(query_one("#d_table").render()) doesn't reflect a DataTable's
        # content (Task 7/9 finding, see _dump's docstring) -- "cause of
        # death" is a COLUMN HEADER, so it's read via ordered_columns, not a
        # cell value.
        table = mon.query_one("#d_table", DataTable)
        assert "cause of death" in _headers(table)
        assert "starvation" in _dump(table)   # the propagated iter-1 row rendered


async def test_back_button_closes_detail():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon.open_program()
        await pilot.pause()
        assert mon.detail_open is True
        await pilot.click("#back")
        await pilot.pause()
        assert mon.detail_open is False


async def test_best_overall_opens_full_table():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon.open_program()
        await pilot.pause()
        table = mon.query_one("#d_table", DataTable)
        # the extra leading "identity" column = every seed x every identity
        assert "identity" in _headers(table)
        assert "wiz-elf-cha-mal" in _dump(table)   # a per-identity row actually rendered


async def test_n1_run_has_no_best_overall_row():
    """I8: a single-identity objective never seeds/moves the union cell
    (harness/archive.py's ``len(identities) > 1`` guard), so BEST OVERALL
    would sit frozen at the AutoAscend baseline while the identity's own row
    improves -- misleading. The row must not render at all."""
    ident = "wiz-elf-cha-mal"
    cfg1 = EvolveConfig(ident, "claude", 3, model="opus", effort="high",
                        operator_version="1.2.7")
    r = Run("r2", cfg1)
    r.apply_state({"phase": "cold-start", "iteration": 0, "identities": [ident],
                   "cells": [{"identity": ident, "score": 0.4, "digest": "d:1"}],
                   "origins": {"d:1": {"kind": "hub", "handle": "clyde", "sha": "11",
                                       "repo": "github.com/t/a", "iteration": None}},
                   "aa_baseline": {ident: 0.3}, "union": None,
                   "cell_results": {ident: []}, "coverage": (1, 1), "cell": None,
                   "generation": 0, "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0,
                   "tokens": 0, "detail": "", "parent_digest": "", "parent_dev": 0.0})
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        table_text = _dump(mon.query_one("#idents", DataTable))
        assert "BEST OVERALL" not in table_text
        assert "Wizard" in table_text   # the identity's own row still renders
