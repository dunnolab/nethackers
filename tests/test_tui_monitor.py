"""Screen-level tests for the reworked RunMonitor: iteration list + Progress/
Mutator Logs/Logs tabs + a clickable Progress table, rendered from a ``Run``.
(The worker->Run reductions themselves are covered by test_tui_run.py.)"""
from __future__ import annotations

from textual.app import App
from textual.widgets import DataTable, Static, TabbedContent, TabPane

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
                   # the union champion's OWN full-coverage union eval (harness/
                   # loop.py's _emit carries archive.union.dev_evidence.results) --
                   # a DIFFERENT program from the per-identity cells' champions
                   # below (different seed/score), matching real cold-start.
                   "union": {"score": 0.48, "digest": "u1", "results": [
                       {"character": i, "trajectory_id": 1, "progress": 0.48,
                        "status": "completed", "end_status": "died",
                        "cause_of_death": "petrification", "max_depth": 7,
                        "turns": 1000, "wall_seconds": 14.0} for i in ids]},
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


async def test_open_best_hub_source_link_uses_the_cold_start_snapshot_not_the_live_cell():
    """Regression: open_best's "hub" branch re-derived the source-link digest
    from the LIVE cell archive (self.run.cells()) -- once a run child takes
    v1's cell, a past-iteration view whose incumbent() is still "hub" (Fix 1's
    snapshot-based incumbent) would look up the CHILD's origin (no repo/sha ->
    "origin unknown") for the link instead of the champion's, even though the
    label/score still say "hub". Must use the cold-start snapshot
    (run.init_cells), matching incumbent()'s own source of truth."""
    from nethackers.harness.loop import IterationResult
    cfg = EvolveConfig("v1", "claude", 3)
    r = Run("r1", cfg)
    hub_origin = {"kind": "hub", "handle": "clyde", "sha": "11",
                  "repo": "github.com/t/a", "iteration": None}
    r.apply_state({
        "phase": "cold-start", "iteration": 0, "identities": ["v1"],
        "cells": [{"identity": "v1", "score": 0.42, "digest": "d1"}],
        "origins": {"d1": hub_origin}, "aa_baseline": {"v1": 0.28},
        "union": None, "cell_results": {"v1": []}, "coverage": (1, 1),
        "cell": None, "generation": 0,
        "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0, "detail": "",
        "parent_digest": "", "parent_dev": 0.0})
    # a run child later takes v1's cell -- the LIVE cells/origins now show the
    # child (kind "run", no repo/sha), overwriting the cold-start snapshot.
    child_origin = {"kind": "run", "handle": "dev", "sha": None, "repo": None, "iteration": 1}
    r.apply_state({
        "phase": "registered", "iteration": 1, "identities": ["v1"],
        "cells": [{"identity": "v1", "score": 0.6, "digest": "child1"}],
        "origins": {"d1": hub_origin, "child1": child_origin},  # merged, as a real emit would be
        "aa_baseline": {"v1": 0.28}, "union": None, "cell_results": {"v1": []},
        "coverage": (1, 1), "cell": "v1", "generation": 1,
        "baseline_dev": 0.0, "best_dev": 0.0, "wins": 1, "tokens": 0, "detail": "",
        "parent_digest": "", "parent_dev": 0.0})
    r.apply_iteration(1, IterationResult(True, "registered", improved=["v1"],
                                         results=[{"character": "v1", "progress": 0.6}]))
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon._select(1)   # viewing iter 1 itself -> incumbent(v1, upto_k=1) is BEFORE the win
        assert mon.run.incumbent("v1", mon.sel_iter)[2] == "hub"   # sanity: still "hub"
        mon.open_best("v1")
        await pilot.pause()
        assert mon.detail_open is True
        src_text = str(mon.query_one("#d_src").render())
        assert "github.com/t/a" in src_text and "11" in src_text   # champion's link
        assert "origin unknown" not in src_text                    # not the child's


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
        text = _dump(table)
        assert "wiz-elf-cha-mal" in text   # a per-identity row actually rendered
        # the union champion's OWN eval (Fix B) -- not the per-identity
        # cells' champions (cell_results' "killed by a newt" seed 0 rows).
        assert "petrification" in text
        assert "killed by a newt" not in text


async def test_best_overall_detail_shows_the_union_champions_own_rows():
    """Regression: show_program's hub branch built the BEST OVERALL table
    from run.iteration_evals(0) -- the per-identity CELLS' own champions --
    instead of the union champion's own cold-start union eval. The union
    champion is generally NOT any identity's own /elites leader (it took no
    per-identity cell here), so the old code showed a DIFFERENT program's
    rows under the union champion's label/score. Must render the union
    champion's OWN per-seed rows (init_union["results"])."""
    ids = ["v1", "v2"]
    cfg = EvolveConfig("v1,v2", "claude", 3)
    r = Run("r1", cfg)
    origins = {f"d:{i}": {"kind": "hub", "handle": "clyde", "sha": "11",
                          "repo": "github.com/t/a", "iteration": None} for i in ids}
    origins["u1"] = {"kind": "hub", "handle": "mikhail", "sha": "33",
                      "repo": "github.com/t/u", "iteration": None}
    r.apply_state({
        "phase": "cold-start", "iteration": 0, "identities": ids,
        "cells": [{"identity": i, "score": 0.9, "digest": f"d:{i}"} for i in ids],
        "origins": origins, "aa_baseline": {i: 0.3 for i in ids},
        "union": {"score": 0.48, "digest": "u1", "results": [
            {"character": "v1", "trajectory_id": 50, "progress": 0.5, "status": "completed",
             "end_status": "died", "ascended": False, "cause_of_death": "starvation",
             "max_depth": 4, "turns": 400, "wall_seconds": 8.0},
            {"character": "v2", "trajectory_id": 60, "progress": 0.46, "status": "completed",
             "end_status": "died", "ascended": False, "cause_of_death": "petrification",
             "max_depth": 5, "turns": 500, "wall_seconds": 9.0},
        ]},
        # each identity's OWN cell champion is a DIFFERENT, higher-scoring
        # program than the union champion -- the pre-fix bug rendered THESE
        # rows under the union champion's label instead.
        "cell_results": {i: [{"character": i, "trajectory_id": 1, "progress": 0.9,
                              "status": "completed", "end_status": "died",
                              "ascended": False, "cause_of_death": "killed by a newt",
                              "max_depth": 6, "turns": 900, "wall_seconds": 12.0}]
                         for i in ids},
        "coverage": (2, 2), "cell": None, "generation": 0,
        "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0, "detail": "",
        "parent_digest": "", "parent_dev": 0.0})
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon.open_program()
        await pilot.pause()
        table = mon.query_one("#d_table", DataTable)
        text = _dump(table)
        assert "50" in text and "60" in text            # union champion's own seeds
        assert "starvation" in text and "petrification" in text
        assert "killed by a newt" not in text           # NOT the per-identity cells' champion


async def test_open_run_and_open_best_open_different_programs():
    """``open_run`` (this iteration's own candidate) and ``open_best`` (the
    incumbent) must open DIFFERENT programs once the incumbent has propagated
    to a prior iteration's win -- not the same detail twice. Regression test
    for open_run, which previously had no coverage at all."""
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
        dv = mon.query_one("#detailview")
        table = mon.query_one("#d_table", DataTable)

        mon.open_best("wiz-elf-cha-mal")
        await pilot.pause()
        best_title, best_rows = dv.border_title, table.row_count

        mon.close_detail()
        await pilot.pause()
        mon.open_run("wiz-elf-cha-mal")
        await pilot.pause()
        run_title, run_rows = dv.border_title, table.row_count

        # best-so-far = the propagated iter-1 win (1 seed row, cause known);
        # this-iteration = iter 2's own candidate, which hasn't started yet
        # (0 rows) -- genuinely different programs, not the same view twice.
        assert best_title == " wiz-elf-cha-mal · run · iter 1 "
        assert run_title == " wiz-elf-cha-mal · iter 2 "
        assert best_title != run_title
        assert best_rows == 1
        assert run_rows == 0


async def test_open_run_live_refresh_gains_rows_then_upgrades_on_completion():
    """§5.5: a RUNNING iteration's open_run() table starts empty, gains a row
    per streamed episode (cause/time pending -> "--"), and -- once the
    iteration is decided -- the SAME still-open table upgrades in place to
    the full cause/depth/turns/time detail. Regression test for
    _refresh_detail_if_open(), which previously had no coverage at all."""
    r = _run()
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal", "val-dwa-law-fem"]
    r.apply_state({"phase": "evaluating-dev", "iteration": 1, "identities": ids,
                   "cells": [{"identity": i, "score": 0.4, "digest": f"d:{i}"} for i in ids],
                   "origins": {}, "aa_baseline": {i: 0.3 for i in ids}, "union": None,
                   "cell_results": {i: [] for i in ids}, "coverage": (3, 3),
                   "cell": "wiz-elf-cha-mal", "generation": 1,
                   "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0, "detail": "",
                   "parent_digest": "", "parent_dev": 0.0})
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon._select(1)
        mon.open_run("wiz-elf-cha-mal")
        await pilot.pause()
        table = mon.query_one("#d_table", DataTable)
        assert table.row_count == 0   # nothing streamed yet

        ep = {"index": 0, "total": 1, "seed": 5, "character": "wiz-elf-cha-mal",
              "progress": 0.3, "status": "died", "turns": 200, "depth": 2}
        r.apply_episode("iter 1/3 · dev", ep)
        mon.render_episode("iter 1/3 · dev", ep)
        await pilot.pause()
        assert table.row_count == 1
        row = table.get_row_at(0)
        assert str(row[0]) == "5"    # seed
        assert str(row[3]) == "—"    # cause of death -- pending
        assert str(row[6]) == "—"    # time -- pending

        result = IterationResult(True, "registered", improved=["wiz-elf-cha-mal"],
            dev_fitness=0.3, results=[{"character": "wiz-elf-cha-mal", "trajectory_id": 5,
                "progress": 0.3, "status": "completed", "end_status": "died",
                "ascended": False, "cause_of_death": "killed by a jackal", "max_depth": 2,
                "turns": 200, "wall_seconds": 9.0}])
        r.apply_iteration(1, result)
        mon.render_iteration(1, result)
        await pilot.pause()
        assert table.row_count == 1        # same row, upgraded in place
        row = table.get_row_at(0)
        assert str(row[3]) == "killed by a jackal"
        assert str(row[4]) == "2"          # depth
        assert str(row[5]) == "200"        # turns
        assert str(row[6]) != "—"          # a real duration now


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


async def test_cold_start_frame_populates_the_progress_table_live():
    # The monitor mounts on the initial (identity-less) state -> empty table.
    # When the FIRST cold-start frame arrives (identities known, no cells scored
    # yet -- loop.py's early emit), render_state must rebuild the Progress table
    # so the identities appear immediately, instead of the table staying empty
    # until the whole cold-start eval finishes.
    # Task 5: Run.identities() now falls back to the resolved objective, so the
    # table already has rows before this first frame -- see the first assertion.
    r = Run("r1", CFG)   # _INITIAL_STATE carries no "identities"
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        dt = mon.query_one("#idents", DataTable)
        assert dt.row_count > 0            # rows now come from the objective before the first state
        ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal", "val-dwa-law-fem"]
        r.apply_state({"phase": "cold-start", "iteration": 0, "identities": ids,
                       "cells": [], "origins": {}, "aa_baseline": {i: 0.3 for i in ids},
                       "union": None, "cell_results": {}, "coverage": (0, 3),
                       "cell": None, "generation": 0, "baseline_dev": 0.0,
                       "best_dev": 0.0, "wins": 0, "tokens": 0, "detail": "",
                       "parent_digest": "", "parent_dev": 0.0})
        mon.render_state()
        await pilot.pause()
        assert dt.row_count > 0                         # populated by the first cold-start frame
        text = _dump(dt)
        assert "wiz-elf-cha-mal" in text and "val-dwa-law-fem" in text


async def test_empty_detail_table_survives_a_click_without_crashing():
    # open_best on an AutoAscend identity -> show_baseline leaves #d_table with
    # NO columns. Clicking it (a header / out-of-bounds click) must NOT crash:
    # a bare DataTable indexes ordered_columns[0] -> IndexError; the ClickTable
    # override returns early for row < 0. Regression for the reported traceback.
    r = Run("r1", EvolveConfig("wiz-elf-cha-mal", "claude", 3))
    r.apply_state({"phase": "cold-start", "iteration": 0, "identities": ["wiz-elf-cha-mal"],
                   "cells": [], "origins": {}, "elite_of": {},
                   "aa_baseline": {"wiz-elf-cha-mal": 0.08}, "union": None,
                   "cell_results": {}, "coverage": (0, 1), "cell": None, "generation": 0,
                   "baseline_dev": 0.0, "best_dev": 0.0, "wins": 0, "tokens": 0,
                   "detail": "", "parent_digest": "", "parent_dev": 0.0})
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon.open_best("wiz-elf-cha-mal")   # AutoAscend floor -> show_baseline (empty table)
        await pilot.pause()
        assert mon.detail_open is True
        dt = mon.query_one("#d_table", DataTable)
        assert len(dt.columns) >= 1                     # never column-less (the fix)
        await pilot.click("#d_table", offset=(3, 0))    # header click -> used to IndexError
        await pilot.pause()
        assert mon.detail_open is True                  # survived, no crash


async def test_the_monitor_opens_on_logs_with_the_now_line():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        tabs = mon.query_one("#tabs", TabbedContent)
        assert tabs.active == "tab_logs"
        assert [p.id for p in tabs.query(TabPane)] == ["tab_logs", "tab_score", "tab_mutator"]
        assert mon.steps_view is not None and "Setup" in mon.steps_view.header
        assert "Setup" in str(mon.query_one("#now", Static).render())


async def test_the_status_line_and_footer_say_where_you_are_and_how_to_leave():
    host = _Host(_run())
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        text = str(mon.query_one("#statusline", Static).render())
        assert text.strip().startswith("viewing setup") and "tokens in" in text
    descriptions = {key: desc for key, _action, desc in RunMonitor.BINDINGS}
    assert descriptions == {"escape": "Dashboard (run keeps going)", "s": "Stop run",
                            "q": "Quit (stops the run)"}


async def test_stop_records_the_request_and_the_now_line_says_so():
    r = _run()
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon.action_stop()
        await pilot.pause()
        assert r.stop.is_set() and r.stop_requested_at is not None
        assert "Stopping" in str(mon.query_one("#now", Static).render())


async def test_logs_follow_the_selected_iteration():
    r = _run()
    ids = ["wiz-elf-cha-mal", "wiz-orc-cha-mal", "val-dwa-law-fem"]
    r.apply_state({**r.state, "phase": "mutating", "iteration": 1, "cell": ids[0]})
    host = _Host(r)
    async with host.run_test(size=(140, 42)) as pilot:
        await pilot.pause()
        mon = host.screen
        mon._select(1)
        await pilot.pause()
        assert mon.steps_view is not None
        assert "Iteration 1 of 3" in mon.steps_view.header
