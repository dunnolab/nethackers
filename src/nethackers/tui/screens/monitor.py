"""The evolution monitor: a left iteration list (init + every iteration, each
marked live/registered/rejected/pending) next to Progress/Mutator Logs/Logs
tabs, rendered from an app-owned ``Run``. Replaces the old cockpit/2-tab
screen -- port of the validated prototype
(docs/superpowers/plans/2026-09-06-evolve-monitor-proto.py's ``MonitorScreen``)
onto real ``Run`` accessors.

Opening it **backfills** the whole view from the run's accumulated state (the
iteration list + the currently-viewed iteration's Progress table), then the
app forwards live worker events (``render_state``/``render_episode``/
``render_log``/``render_iteration``) while this screen is on top. ``esc``
detaches the view -- the run keeps running -- and ``s`` stops it. The screen
holds no worker (the app owns it).

Per-seed detail (clicking a program cell to see its per-seed breakdown) is a
STUB in this task: ``DetailView`` composes its widgets but never renders, and
``open_best``/``open_run``/``open_program`` are no-ops -- Task 10 fills these
in. The click-routing itself (``on_data_table_cell_selected`` et al.) is real
now, so Task 10 only has to fill the open_*/show_* bodies.
"""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    OptionList,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.data_table import RowKey
from textual.widgets.option_list import Option

from nethackers.harness.loop import IterationResult
from nethackers.tui import status as S
from nethackers.tui.run import EvalView, Run
from nethackers.tui.screens._clicktable import ClickTable

# The iteration's coding-agent transcript (``run.logs[tag]``), by prettified
# line "kind" -- the same map the old monitor used for its agent-log tab.
_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim",
               "brief": "#d2a24c"}


class DetailView(Vertical):
    """Per-program evaluation window (embedded panel, not a pushed screen):
    a per-seed table for one program, or the BEST OVERALL program's full
    seed x identity table. STUB in this task -- Task 10 renders it; here it
    only needs to compose so the screen mounts and stays reachable."""

    def compose(self) -> ComposeResult:
        yield Static(id="d_head")
        yield Static(id="d_src")
        yield DataTable(id="d_table", zebra_stripes=True, cursor_type="row")
        yield Button("‹ Back to optimization", id="back", variant="primary")

    def show_eval(self, rows: list[dict], total: int, title: str, src: str,
                  live_ref: tuple[int, str] | None = None) -> None:
        """STUB (Task 10): render the per-seed table for one program."""

    def show_program(self, run: Run, info: tuple[float, str, str, int | None]) -> None:
        """STUB (Task 10): render BEST OVERALL's full seed x identity table."""

    def refresh_live(self) -> None:
        """STUB (Task 10): re-render a live (in-progress) eval in place."""

    def close_detail(self) -> None:
        """STUB (Task 10)."""


class RunMonitor(Screen):
    """Iteration list + Progress/Mutator Logs/Logs tabs, rendered from a
    ``Run``. Holds no worker."""

    CSS = """
    RunMonitor #titlebar { height: 1; }
    RunMonitor #title_name { width: 1fr; padding: 0 1; }
    RunMonitor #title_mutator { width: auto; padding: 0 1; }
    RunMonitor #stage { height: 1fr; }
    RunMonitor #main { height: 1fr; }
    RunMonitor #left { width: 30; }
    RunMonitor #iters { height: 1fr; }
    RunMonitor #right { width: 1fr; }
    RunMonitor #progress_pane { padding: 0 1; }
    RunMonitor #idents { height: 1fr; }
    RunMonitor #detailview { height: 1fr; padding: 1 2; }
    RunMonitor #d_head { height: auto; padding: 0 0 1 0; }
    RunMonitor #d_src { height: auto; color: #7c745f; padding: 0 0 1 0; }
    RunMonitor #d_table { height: 1fr; }
    RunMonitor #back { margin: 1 0 0 0; width: auto; }
    RunMonitor #proclog { height: 1fr; }
    RunMonitor TabbedContent { height: 1fr; }
    RunMonitor DataTable > .datatable--cursor {
        background: #d2a24c; color: #0b0b0e; text-style: bold;
    }
    RunMonitor DataTable:focus > .datatable--cursor { background: #ffd54a; color: #0b0b0e; }
    """
    BINDINGS = [
        ("escape", "nav_back", "Back"),
        ("s", "stop", "Stop"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, run: Run) -> None:
        super().__init__()
        self.run = run
        self._row_map: list[tuple[str, str | None]] = []   # row index -> (kind, ident)
        self._row_keys: dict[str, RowKey] = {}              # ident -> its table row key
        self._last_cursor_row = 0
        self._mutlog_shown = 0    # #mutlog lines already written, for the viewed iteration
        # False until on_mount finishes building the view. push_screen returns
        # (synchronously) before this screen's own compose/on_mount actually
        # runs (that's scheduled on the message pump) -- so a run whose worker
        # fires its very first on_state callback fast enough can reach here
        # via app._on_run_state -> render_state BEFORE #iters/#idents exist,
        # raising NoMatches and (uncaught, since it crosses call_from_thread
        # back into the worker thread) failing the whole run. Confirmed via a
        # repro script: ~50% hit rate for this specific screen's deeper widget
        # tree (the old cockpit's shallower one apparently never raced this
        # hard). Skipping renders until ready is safe -- Run already holds the
        # latest state regardless (apply_state/apply_episode/apply_log ran
        # unconditionally before this guard), and on_mount's own _select(...)
        # backfills from that current state as soon as it runs.
        self._ready = False
        self.sel_iter = next(
            (k for k in range(run.cfg.iterations + 1) if run.iteration_status(k) == "running"),
            0)

    def compose(self) -> ComposeResult:
        with Horizontal(id="titlebar"):
            yield Static("[b #d2a24c]⚔ NetHackers · evolve monitor[/]", id="title_name")
            yield Static(id="title_mutator")
        with Vertical(id="stage"):
            with Horizontal(id="main"):
                with Vertical(id="left", classes="panel"):
                    yield OptionList(id="iters")
                with Vertical(id="right"), TabbedContent(id="tabs"):
                    with TabPane("Progress", id="tab_score"), Vertical(id="progress_pane"):
                        yield ClickTable(id="idents", cursor_type="cell",
                                         zebra_stripes=True, classes="panel")
                    with TabPane("Mutator Logs", id="tab_mutator"):
                        yield RichLog(id="mutlog", classes="panel",
                                     wrap=True, markup=True, highlight=False)
                    with TabPane("Logs", id="tab_proclog"):
                        yield Static(id="proclog", classes="panel")
            yield DetailView(id="detailview", classes="panel")
        yield Static(id="statusline", classes="statusline")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#left").border_title = " iterations "
        self.query_one("#title_mutator", Static).update(S.mutator_title(self.run.cfg))
        self.query_one("#detailview").display = False
        self._render_iters()
        # #idents/#mutlog/#proclog live inside a TabbedContent's TabPanes,
        # which -- per Textual -- aren't mounted yet during the SCREEN's own
        # on_mount (matches the old monitor's identical, already-documented
        # hazard for its own TabbedContent panes); retry next frame until
        # they exist. Guaranteed to terminate: all three are unconditional in
        # compose().
        self.call_after_refresh(self._backfill)
        self.set_interval(1.0, self._tick)

    def _backfill(self) -> None:
        try:
            idents = self.query_one("#idents", ClickTable)
            self.query_one("#mutlog").border_title = " mutator · coding-agent transcript "
            self.query_one("#proclog").border_title = " process log · what's happening now "
        except NoMatches:
            self.call_after_refresh(self._backfill)
            return
        idents.add_column("identity", key="id")
        idents.add_column("best so far", key="best")
        idents.add_column("this iteration", key="run")
        idents.border_title = " progress by identity "
        idents._valid_fn = self._valid_cell        # hover only on clickable cells
        # #idents now has its columns -- safe to render into it. Flip the
        # flag BEFORE calling _select (below) so this, its own first real
        # render, isn't itself skipped by the on_option_list_option_*
        # handlers' guard.
        self._ready = True
        self._select(self.sel_iter)

    def action_stop(self) -> None:
        self.run.stop.set()
        self.app.notify(f"stopping run {self.run.rid} …", timeout=4)

    def action_nav_back(self) -> None:
        self.dismiss()
        # the dashboard resumes with #nav focused (Textual restores it) --
        # tell it to reclaim navigate mode so ←/→ don't get eaten by Tabs.
        reassert = getattr(self.app, "reassert_navigate", None)
        if callable(reassert):
            self.app.call_after_refresh(reassert)

    # ---- rendering: iteration list -------------------------------------------
    def _render_iters(self) -> None:
        ol = self.query_one("#iters", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for k in range(self.run.cfg.iterations + 1):
            label = "init" if k == 0 else f"iter {k}"
            status = self.run.iteration_status(k)
            if status == "init":
                mark, color, tag, disabled = "✓", "#00a000", "", False
            elif status == "registered":
                mark, color, tag, disabled = "✓", "#00a000", "  [#00a000]registered[/]", False
            elif status == "rejected":
                mark, color, tag, disabled = "✗", "#7c745f", "  [dim]rejected[/]", False
            elif status == "running":
                mark, color, tag, disabled = "▶", "#ffd54a", "  [#ffd54a]live[/]", False
            else:  # pending
                mark, color, tag, disabled = "·", "#7c745f", "", True
            ol.add_option(Option(
                Text.from_markup(f"[{color}]{mark}[/] [b]{label}[/]{tag}"),
                id=f"it::{k}", disabled=disabled))
        ol.highlighted = keep if keep is not None else self.sel_iter

    # ---- rendering: Progress table --------------------------------------------
    def _row_eval(self, ident: str, evals: dict[str, EvalView]) -> EvalView:
        """The identity's eval for the VIEWED iteration -- suppressing a
        running iteration's foreign/stale batch (the previous iteration's
        leftover rows during "mutating", or the gate's smoke-test episodes
        during "gating") outside the "evaluating-dev" phase, mirroring the
        old monitor's own gate (monitor.py:215, pre-rework)."""
        ev = evals[ident]
        if (self.run.iteration_status(self.sel_iter) == "running"
                and self.run.state.get("phase") != "evaluating-dev"):
            return EvalView(ident, ev.total, [])
        return ev

    def _rebuild_score(self) -> None:
        """Full rebuild of the Progress table -- only on iteration change /
        mount / a completed iteration (a discrete event), never on a live
        in-place tick (``_update_score`` handles that)."""
        is_init = self.sel_iter == 0
        t = self.query_one("#idents", ClickTable)
        coord = t.cursor_coordinate
        t.clear()
        self._row_map = []
        self._row_keys = {}

        # BEST OVERALL (the union cell) sits IN the table, first (openable)
        # row -- it UPDATES as the run finds a child with a better average.
        bo = self.run.best_overall(self.sel_iter)
        t.add_row(S.best_overall_cell(bo), Text.from_markup(f"[dim]x̄[/] [b]{bo[0]:.2f}[/]"),
                  "", key="ov")
        self._row_map.append(("overall", None))

        target = None if is_init else self.run.iter_target(self.sel_iter)
        evals = self.run.iteration_evals(self.sel_iter)
        for role in self.run.roles_present():
            t.add_row(Text.from_markup(f"[b #d2a24c]{S.role_full(role)}[/]"), "", "",
                      key=f"role:{role}")
            self._row_map.append(("role", None))
            for ident in (i for i in self.run.identities() if self.run.role_of(i) == role):
                inc = self.run.incumbent(ident, self.sel_iter)
                ev = self._row_eval(ident, evals)
                name = (f"[b #ffd54a]✎ {ident}[/]" if ident == target else f"[b]  {ident}[/]")
                best_c = S.best_cell(inc)
                run_c = S.run_cell(ev.avg, ev.revealed, ev.total, inc[0], is_init, ev.done)
                rk = t.add_row(Text.from_markup(name), best_c, run_c, key=f"id:{ident}")
                self._row_keys[ident] = rk
                self._row_map.append(("ident", ident))
        # keep the cursor on a program cell
        if coord is not None and self._valid_cell(coord.row, coord.column):
            t.move_cursor(row=coord.row, column=coord.column)
            self._last_cursor_row = coord.row
        else:
            first = next((r for r, (k, _) in enumerate(self._row_map) if k == "ident"), 0)
            t.move_cursor(row=first, column=1)
            self._last_cursor_row = first

    def _update_score(self) -> None:
        """In-place update of only the "this iteration" cells, so the cursor /
        highlight / focus are preserved and the table doesn't flicker on
        every live tick."""
        is_init = self.sel_iter == 0
        t = self.query_one("#idents", ClickTable)
        evals = self.run.iteration_evals(self.sel_iter)
        for ident in self.run.identities():
            inc = self.run.incumbent(ident, self.sel_iter)
            ev = self._row_eval(ident, evals)
            rk = self._row_keys.get(ident)
            if rk is not None:
                t.update_cell(rk, "run", S.run_cell(ev.avg, ev.revealed, ev.total, inc[0],
                                                     is_init, ev.done), update_width=False)

    # ---- rendering: Mutator Logs / Logs tabs ----------------------------------
    def _render_mutator(self) -> None:
        log = self.query_one("#mutlog", RichLog)
        log.clear()
        lines = self.run.logs.get(self.run.tag(self.sel_iter), [])
        for kind, text in lines:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._mutlog_shown = len(lines)

    def _render_proclog(self) -> None:
        self.query_one("#proclog", Static).update(
            Text.from_markup("\n".join(self._proclog_lines(self.sel_iter))))

    def _proclog_lines(self, k: int) -> list[str]:
        status = self.run.iteration_status(k)
        if k == 0:
            lines = ["[dim]cold-start · seeding identities from the hub[/]"]
            for ident in self.run.identities():
                inc = self.run.incumbent(ident, 0)
                lines.append(f"  {ident:<18} ← {inc[1]}   ({inc[0]:.2f})")
            bo = self.run.best_overall(0)
            lines.append(f"best overall = {bo[1]}   x̄ {bo[0]:.2f}")
            lines.append("[dim]no mutation at init — evaluating the seeds[/]")
            return lines
        if status == "pending":
            return ["[dim]iteration not started[/]"]
        meta = self.run.iter_meta.get(k) or {}
        cfg = self.run.cfg
        bits = [f"operator={cfg.backend}"]
        if cfg.model:
            bits.append(f"model={cfg.model}")
        if cfg.effort:
            bits.append(f"effort={cfg.effort}")
        lines = [f"[dim]── iter {k} ──[/]",
                 f"seed parent   ← {meta.get('seed_desc', '—')}",
                 "invoke mutator   " + "  ".join(bits)]
        evals = self.run.iteration_evals(k)
        for ident in self.run.identities():
            ev = self._row_eval(ident, evals)
            if ev.revealed == 0:
                lines.append(f"  {ident:<18}  0/{ev.total}  [dim]queued[/]")
            elif ev.done:
                lines.append(f"  {ident:<18}  {ev.revealed:>2}/{ev.total}  x̄ {ev.avg:.2f}")
            else:
                lines.append(f"  {ident:<18}  {ev.revealed:>2}/{ev.total}  "
                             f"x̄ {ev.avg:.2f}  [dim]⊙[/]")
        if status == "running":
            lines.append("[dim]… evaluating …[/]")
        elif status == "registered":
            lines.append("[green]gate: registered · new best[/]")
        else:
            lines.append("[dim]gate: rejected · no improvement[/]")
        return lines

    def _render_statusline(self) -> None:
        self.query_one("#statusline", Static).update(
            S.token_subline(self.run.token_usage(), self.run.run_time()))

    def _select(self, index: int) -> None:
        self.sel_iter = index
        self._rebuild_score()
        self._render_mutator()
        self._render_proclog()
        self._render_statusline()

    # ---- detail open/close (STUBS -- Task 10) --------------------------------
    def open_best(self, ident: str) -> None:
        """STUB (Task 10): open the incumbent ("best so far") program."""

    def open_run(self, ident: str) -> None:
        """STUB (Task 10): open this iteration's candidate for ``ident``."""

    def open_program(self) -> None:
        """STUB (Task 10): open the BEST OVERALL (union cell) program."""

    # ---- events ---------------------------------------------------------------
    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if not self._ready:
            # populating #iters (on_mount's _render_iters, before _backfill
            # gives #idents its columns) auto-highlights the first option,
            # firing this before #idents is ready -- _backfill's own
            # _select(self.sel_iter) will render correctly once ready either
            # way, so this early auto-fire is safely ignored.
            return
        oid = event.option.id or ""
        if oid.startswith("it::"):
            self._select(int(oid.split("::")[1]))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._ready:
            return
        oid = event.option.id or ""
        if oid.startswith("it::"):
            self._select(int(oid.split("::")[1]))

    def _valid_cell(self, row: int, col: int) -> bool:
        if not (0 <= row < len(self._row_map)):
            return False
        kind = self._row_map[row][0]
        if kind == "overall":
            return col in (0, 1)
        if kind == "ident":
            return col == 1 if self.sel_iter == 0 else col in (1, 2)  # init: no "this iteration"
        return False

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        # only program cells highlight: BEST OVERALL (row 0, col 0/1) and each
        # identity's two programs (col 1/2). Snap off everything else.
        if getattr(event.data_table, "id", None) != "idents":
            return
        n = len(self._row_map)
        row, col = event.coordinate.row, event.coordinate.column
        if self._valid_cell(row, col):
            self._last_cursor_row = row
            return
        going_up = row < self._last_cursor_row
        order = (list(range(row, -1, -1)) + list(range(row + 1, n))) if going_up \
            else (list(range(row, n)) + list(range(row - 1, -1, -1)))
        for r in order:
            if not (0 <= r < n):
                continue
            kind = self._row_map[r][0]
            if kind == "overall":
                self._last_cursor_row = r
                event.data_table.move_cursor(row=r, column=0 if col <= 0 else 1)
                return
            if kind == "ident":
                self._last_cursor_row = r
                tcol = 1 if (self.sel_iter == 0 or col <= 1) else 2
                event.data_table.move_cursor(row=r, column=tcol)
                return

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        # NOTE: a single click on a ClickTable posts CellSelected TWICE
        # (Textual 8.2.8 MRO/cursor-dispatch quirk, confirmed in Task 7) -- this
        # handler is a pure function of event.coordinate -> open_*, so a
        # double-fire is harmless (opening the same thing twice).
        row, col = event.coordinate.row, event.coordinate.column
        if row >= len(self._row_map):
            return
        kind, ident = self._row_map[row]
        if kind == "overall":
            self.open_program()
        elif kind == "ident" and ident is not None:
            if col == 1:                              # "best so far" program
                self.open_best(ident)
            elif col == 2 and self.sel_iter != 0:      # "this iteration" (none at init)
                self.open_run(ident)

    # ---- live renders (forwarded by the app while this screen is on top) ------
    def render_state(self) -> None:
        if not self._ready:
            return   # pre-mount race (see __init__) -- on_mount will backfill
        self._render_iters()
        if self.run.iteration_status(self.sel_iter) == "running":
            self._update_score()
        self._render_statusline()

    def render_episode(self, label: str, ep: dict) -> None:
        if not self._ready:
            return
        self._update_score()
        self._render_proclog()

    def render_log(self, tag: str) -> None:
        if not self._ready:
            return
        if tag != self.run.tag(self.sel_iter):
            return   # not the viewed iteration -- its log isn't on screen
        log = self.query_one("#mutlog", RichLog)
        lines = self.run.logs.get(tag, [])
        for kind, text in lines[self._mutlog_shown:]:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._mutlog_shown = len(lines)

    def render_iteration(self, iteration: int, result: IterationResult) -> None:
        if not self._ready:
            return
        self._render_iters()   # status rollover: running -> registered/rejected
        if iteration <= self.sel_iter:
            # a decided iteration at-or-before the viewed one: its own row
            # needs its final (not live-streaming) rendering, and/or it may
            # have raised the incumbent/BEST OVERALL baseline the viewed
            # iteration's row is computed against.
            self._rebuild_score()

    def _tick(self) -> None:
        # the run-time clock + cumulative tokens advance every second even
        # between worker events -- keep the status line's subline live.
        self._render_statusline()
