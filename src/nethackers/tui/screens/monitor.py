"""The evolution monitor: a left iteration list (setup + every iteration, each
marked live / improved / no gain / failed test / agent failed / error /
stopped / not run) next to Logs · Progress · Mutator Logs tabs, rendered from
an app-owned ``Run``. Replaces the old cockpit/2-tab screen, built on real
``Run`` accessors.

Opening it **backfills** the whole view from the run's accumulated state (the
iteration list + the currently-viewed iteration's Progress table), then the
app forwards live worker events (``render_state``/``render_episode``/
``render_log``/``render_iteration``) while this screen is on top. ``esc``
detaches the view -- the run keeps running -- and ``s`` stops it. The screen
holds no worker (the app owns it).

Per-seed detail (clicking a program cell to see its per-seed breakdown) is an
embedded, toggled ``DetailView`` panel (not a pushed screen -- the Textual
8.2.8 None-visual-on-a-toggled-panel gotcha, guarded in ``_clicktable.py``).
``open_best``/``open_run``/``open_program`` build the per-seed (or, for BEST
OVERALL, full seed x identity) table from the ``Run`` accessors and hand it to
``DetailView``; a "back" button (no shortcut) returns to the tabs.
"""
from __future__ import annotations

from statistics import pstdev

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
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
from nethackers.tui import status as S, story
from nethackers.tui.run import EvalView, Run
from nethackers.tui.screens._clicktable import ClickTable

# The iteration's coding-agent transcript (``run.logs[tag]``), by prettified
# line "kind" -- the same map the old monitor used for its agent-log tab.
_KIND_STYLE = {"assistant": "", "tool": "cyan", "result": "green b", "meta": "dim",
               "brief": "#d2a24c"}


def _section_name(k: int) -> str:
    return "setup" if k == 0 else f"iter {k}"


def _section_renderable(view: story.SectionView) -> Group:
    """A story.SectionView as the Logs tab draws it: header, intro, then the
    step rows in a mark | label | right-aligned-duration grid, then a footer."""
    parts: list = [Text.from_markup(view.header)]
    if view.intro:
        parts.append(Text.from_markup(view.intro))
    if view.rows:
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=1, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right", width=8, no_wrap=True)
        for row in view.rows:
            grid.add_row(Text.from_markup(row.mark), Text.from_markup(row.label),
                         Text(row.dur, style="#7c745f"))
        parts += [Text(""), grid]
    if view.footer:
        parts += [Text(""), Text.from_markup(view.footer)]
    return Group(*parts)


def _seed_cells(row: dict) -> list:
    """One per-seed row's cells (seed … time), shared by every detail table."""
    glyph, color = S.status_glyph(row["status"])
    return [str(row["seed"]), f'{row["progress"]:.2f}',
            Text(f'{glyph} {row["status"]}', style=color),
            Text(row["cause"] or "—", style="#7c745f" if not row["cause"] else ""),
            str(row["depth"]) if row["depth"] is not None else "—",
            f'{row["turns"]:,}' if row["turns"] is not None else "—",
            S.ep_time(row["time"]) if row["time"] is not None else "—"]


class DetailView(Vertical):
    """Per-program evaluation window (embedded panel, not a pushed screen):
    a per-seed table for one program, or the BEST OVERALL program's full
    seed x identity table. Holds the ``Run`` (set once, at construction --
    mirrors ``RunMonitor.run``) purely so ``refresh_live`` can re-pull a
    still-streaming eval's rows without the screen having to hand them over
    on every episode."""

    def __init__(self, run: Run, *, id: str | None = None,
                 classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.run = run
        self.kind = "eval"                    # eval | program | baseline | candidate
        # (iteration, ident) while streaming a single identity's eval (open_run);
        # (iteration, None) while streaming the BEST OVERALL candidate's table
        # across every identity (open_candidate). None once static.
        self._live: tuple[int, str | None] | None = None
        self._src = ""

    def compose(self) -> ComposeResult:
        yield Static(id="d_head")
        yield Static(id="d_src")
        # NB: this table must never be left column-less -- a header/out-of-bounds
        # click on a DataTable with no columns crashes Textual 8.2.x
        # (ordered_columns[idx] -> IndexError). Every show_* below adds columns.
        yield DataTable(id="d_table", zebra_stripes=True, cursor_type="row")
        yield Button("‹ Back to optimization", id="back", variant="primary")

    def show_eval(self, rows: list[dict], total: int, title: str, src: str,
                  live_ref: tuple[int, str] | None = None) -> None:
        """One program's per-seed table (no xp -- D2)."""
        self.kind = "eval"
        self._live = live_ref
        self._src = src
        self.border_title = title
        t = self.query_one("#d_table", DataTable)
        t.clear(columns=True)
        t.add_columns("seed", "progress", "status", "cause of death",
                      "depth", "turns", "time")
        self._render_eval(rows, total)

    def show_baseline(self, title: str, score: float,
                      per_identity: dict[str, float] | None = None) -> None:
        """D5: AutoAscend is a hub-owned reference score, not a local tree --
        there is nothing to re-run, so no per-seed table. Show the average +
        an honest note; for BEST OVERALL, also list the per-identity AutoAscend
        floor so the panel isn't blank."""
        self.kind = "baseline"
        self._live = None
        self.border_title = title
        self.query_one("#d_head", Static).update(Text.from_markup(
            f"avg [b #ffd54a]{score:.2f}[/]   [dim]AutoAscend baseline · no per-seed breakdown[/]"))
        self.query_one("#d_src", Static).update(Text.from_markup(
            "[dim]source[/]  [dim]AutoAscend baseline · not a repository[/]"))
        t = self.query_one("#d_table", DataTable)
        t.clear(columns=True)
        if per_identity:
            t.add_column("identity", width=24)
            t.add_column("AutoAscend avg", width=16)
            for ident in sorted(per_identity):
                t.add_row(ident, f"{per_identity[ident]:.2f}")
        else:
            # never leave the table column-less (clicking an empty DataTable's
            # header crashes Textual -- see compose()).
            t.add_column("AutoAscend baseline", width=44)
            t.add_row("hub reference score · no per-seed breakdown")

    def show_program(self, run: Run, info: tuple[float, str, str, int | None]) -> None:
        """The BEST OVERALL (union-cell) program's FULL table -- every seed on
        every identity: the hub champion's cold-start union eval, or a run
        child's own eval once it has taken the union cell."""
        score, label, kind, j = info
        self.kind = "program"
        self._live = None
        if kind == "aa":
            self.show_baseline(f" BEST OVERALL · {label} ", score,
                               per_identity=run.aa_baseline())
            return
        self.border_title = f" BEST OVERALL · {label} · all evaluations "
        t = self.query_one("#d_table", DataTable)
        t.clear(columns=True)
        t.add_columns("identity", "seed", "progress", "status", "cause of death",
                      "depth", "turns", "time")
        idents = run.identities()
        evals: dict[str, EvalView] = {}
        if kind == "run" and j is not None:
            evals = run.iteration_evals(j)
            src = (f"[dim]source[/]  [link=file:///runs/{run.rid}/iter{j}/bot.py]"
                   f"bot.py ↗ (this run · iter {j})[/]")
        elif kind == "hub":
            evals = run.union_evals()
            digest = (run.init_union or {}).get("digest")
            origin = (run.origins().get(digest) or {}) if digest else {}
            repo, sha = origin.get("repo"), origin.get("sha")
            src = (f"[dim]source[/]  [link=https://{repo}/commit/{sha}]{repo}@{sha} ↗[/]"
                   if repo and sha else "[dim]source[/]  [dim]origin unknown[/]")
        else:
            # kind == "run" but best_overall() couldn't resolve WHICH iteration
            # (its upto_k propagation only re-derives a STRICT improvement over
            # the already-current union score, so a union held by an earlier
            # "run" win it can't re-beat leaves j None) -- render the table
            # honestly empty rather than guessing at the wrong iteration.
            src = "[dim]source[/]  [dim]origin iteration unknown[/]"
        self.query_one("#d_head", Static).update(Text.from_markup(
            f"[b #d2a24c]{label}[/]   avg [b #ffd54a]{score:.2f}[/]   "
            f"[dim]{len(idents)} identities[/]"))
        self.query_one("#d_src", Static).update(Text.from_markup(src))
        for ident in idents:
            ev = evals.get(ident)
            if ev is None:
                continue
            for row in ev.rows:
                t.add_row(ident, *_seed_cells(row))

    def refresh_live(self) -> None:
        """A currently-streaming eval (``open_run``'s one identity) or this
        iteration's BEST OVERALL candidate (``open_candidate``'s every
        identity) updates in place, re-pulling its rows from the ``Run``;
        the incumbent's full table and the AutoAscend baseline note are
        static snapshots (re-opened, not ticked)."""
        if self._live is None or not self.display or self.kind not in ("eval", "candidate"):
            return
        it, ident = self._live
        if self.kind == "candidate":
            self._render_candidate(it)
        else:
            assert ident is not None
            ev = self.run.iteration_evals(it)[ident]
            self._render_eval(ev.rows, ev.total)

    def _still_playing_suffix(self, shown: int, total: int) -> str:
        """Ruling 16(a): "   still playing…" only for a genuinely running
        run -- a finished (crashed/stopped) run's cut-short batch never
        claims to still be playing, whatever its row count. The one place
        this honesty rule lives, shared by _render_eval and
        _render_candidate so it can't drift between them (Ruling 18)."""
        done = total > 0 and shown >= total
        return "" if done or not self.run.running else "   [dim]still playing…[/]"

    def _render_eval(self, rows: list[dict], total: int) -> None:
        if not rows:
            head = Text.from_markup("[dim]no games yet[/]")
        else:
            scores = [float(r["progress"]) for r in rows]
            avg = sum(scores) / len(scores)
            std = pstdev(scores) if len(scores) > 1 else 0.0
            more = self._still_playing_suffix(len(rows), total)
            head = Text.from_markup(
                f"avg [b #ffd54a]{avg:.2f}[/]   std [b]{std:.2f}[/]   "
                f"[b]{len(rows)}/{total}[/] games{more}")
        self.query_one("#d_head", Static).update(head)
        self.query_one("#d_src", Static).update(Text.from_markup(self._src))
        t = self.query_one("#d_table", DataTable)
        t.clear()
        for row in rows:
            t.add_row(*_seed_cells(row))

    def show_candidate(self, run: Run, k: int) -> None:
        """Iteration k's candidate across every identity: the average the
        BEST OVERALL row's "this iteration" cell shows. Live while the
        iteration is still evaluating -- refresh_live re-renders it in
        place (keeping the columns), the same way open_run's own-candidate
        table does (Ruling 16(b))."""
        self.kind = "candidate"
        self._live = (k, None)
        self.border_title = f" BEST OVERALL candidate · iter {k} · all evaluations "
        t = self.query_one("#d_table", DataTable)
        t.clear(columns=True)
        t.add_columns("identity", "seed", "progress", "status", "cause of death",
                      "depth", "turns", "time")
        self._src = (f"[dim]source[/]  [link=file:///runs/{run.rid}/iter{k}/bot.py]"
                     f"bot.py ↗ (this run · iter {k})[/]")
        self._render_candidate(k)

    def _render_candidate(self, k: int) -> None:
        """The BEST OVERALL candidate table's head + rows, from iteration
        k's own dev batch (running, decided or crashed) -- shared by
        show_candidate and refresh_live so a still-streaming candidate
        ticks up in place without losing its columns."""
        evals = self.run.iteration_evals(k)
        rows = [(ident, row) for ident in self.run.identities() for row in evals[ident].rows]
        total = sum(view.total for view in evals.values())
        if rows:
            avg = sum(float(r["progress"]) for _i, r in rows) / len(rows)
            more = self._still_playing_suffix(len(rows), total)
            head = f"avg [b #ffd54a]{avg:.2f}[/]   [b]{len(rows)}/{total}[/] games{more}"
        else:
            head = "[dim]no games yet[/]"
        self.query_one("#d_head", Static).update(Text.from_markup(head))
        self.query_one("#d_src", Static).update(Text.from_markup(self._src))
        t = self.query_one("#d_table", DataTable)
        t.clear()
        for ident, row in rows:
            t.add_row(ident, *_seed_cells(row))


class RunMonitor(Screen):
    """Iteration list + Logs · Progress · Mutator Logs tabs, rendered from a
    ``Run``. Holds no worker."""

    CSS = """
    RunMonitor #titlebar { height: 1; }
    RunMonitor #title_name { width: 1fr; padding: 0 1; }
    RunMonitor #title_mutator { width: auto; padding: 0 1; }
    RunMonitor #now { height: auto; padding: 0 1; background: #20202b; }
    RunMonitor #stage { height: 1fr; }
    RunMonitor #main { height: 1fr; }
    RunMonitor #left { width: 38; }
    RunMonitor #iters { height: 1fr; }
    RunMonitor #right { width: 1fr; }
    RunMonitor #progress_pane { padding: 0 1; }
    RunMonitor #idents { height: 1fr; }
    RunMonitor #legend { height: auto; color: #7c745f; padding: 0 1; }
    RunMonitor #detailview { height: 1fr; padding: 1 2; }
    RunMonitor #d_head { height: auto; padding: 0 0 1 0; }
    RunMonitor #d_src { height: auto; color: #7c745f; padding: 0 0 1 0; }
    RunMonitor #d_table { height: 1fr; }
    RunMonitor #back { margin: 1 0 0 0; width: auto; }
    RunMonitor #steps_scroll { height: 1fr; }
    RunMonitor TabbedContent { height: 1fr; }
    RunMonitor DataTable > .datatable--cursor {
        background: #d2a24c; color: #0b0b0e; text-style: bold;
    }
    RunMonitor DataTable:focus > .datatable--cursor { background: #ffd54a; color: #0b0b0e; }
    """
    BINDINGS = [
        ("escape", "nav_back", "Dashboard (run keeps going)"),
        ("s", "stop", "Stop run"),
        ("q", "app.quit", "Quit (stops the run)"),
    ]

    def __init__(self, run: Run) -> None:
        super().__init__()
        self.run = run
        self._row_map: list[tuple[str, str | None]] = []   # row index -> (kind, ident)
        self._row_keys: dict[str, RowKey] = {}              # ident -> its table row key
        self._clickable: set[tuple[int, int]] = set()   # (row, col) cells that open detail
        self._score_sig: tuple | None = None             # table structure last built
        self._last_cursor_row = 0
        self._mutlog_shown = 0    # #mutlog lines already written, for the viewed iteration
        self._steps_view: story.SectionView | None = None
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
        self._iters_sig: tuple | None = None
        # Follow the live run -- setup, then each iteration as it starts --
        # until the user picks another section; picking the live one resumes.
        self.following = True
        live = self._live_section()
        self.sel_iter = live if live is not None else (0 if run.running else self._last_ran())

    def compose(self) -> ComposeResult:
        with Horizontal(id="titlebar"):
            yield Static("[b #d2a24c]⚔ NetHackers · evolve monitor[/]", id="title_name")
            yield Static(id="title_mutator")
        yield Static(id="now")   # the always-visible "what's happening now" line
        with Vertical(id="stage"):
            with Horizontal(id="main"):
                with Vertical(id="left", classes="panel"):
                    yield OptionList(id="iters")
                with Vertical(id="right"), TabbedContent(id="tabs", initial="tab_logs"):
                    with TabPane("Logs", id="tab_logs"), VerticalScroll(
                            id="steps_scroll", classes="panel"):
                        yield Static(id="steps")
                    with TabPane("Progress", id="tab_score"), Vertical(id="progress_pane"):
                        yield ClickTable(id="idents", cursor_type="cell",
                                         zebra_stripes=True, classes="panel")
                        yield Static(story.LEGEND, id="legend")
                    with TabPane("Mutator Logs", id="tab_mutator"):
                        yield RichLog(id="mutlog", classes="panel",
                                     wrap=True, markup=True, highlight=False)
            yield DetailView(self.run, id="detailview", classes="panel")
        yield Static(id="statusline", classes="statusline")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#left").border_title = " iterations "
        self.query_one("#title_mutator", Static).update(S.mutator_title(self.run.cfg))
        self.query_one("#detailview").display = False
        self._render_iters()
        # #idents/#mutlog/#steps_scroll live inside a TabbedContent's TabPanes,
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
            self.query_one("#steps_scroll").border_title = " what's happening "
        except NoMatches:
            self.call_after_refresh(self._backfill)
            return
        # Explicit widths: Textual 8.2.x auto_width columns defer their
        # content-width measurement to idle, so at first paint they truncate
        # cells to the *header* width ("sam-hu", "vkurenkov @" with the score
        # cut). Fixed widths render the full identity / champion@sha / score.
        # Budget (Ruling 19/20): the app's real, THEMED viewport at 120x34 is
        # 76 columns (#left is fixed at 38; .panel's border + #progress_pane's
        # padding cost the rest) -- DataTable adds 1 cell of padding on each
        # side of each column, so 17+24+29 = 70 + 6 = 76, an exact fit.
        # Accepted caveat: an objective with enough identities to scroll this
        # table VERTICALLY costs 2 more columns for Textual's scrollbar,
        # which clips the tail of "▲ new best" in "this iteration" -- the
        # score and the game count, the load-bearing parts, stay visible.
        idents.add_column("identity", key="id", width=17)
        idents.add_column("best so far", key="best", width=24)
        idents.add_column("this iteration", key="run", width=29)
        idents.border_title = " progress by identity "
        idents._valid_fn = self._valid_cell        # hover only on clickable cells
        # #idents now has its columns -- safe to render into it. Flip the
        # flag BEFORE calling _select (below) so this, its own first real
        # render, isn't itself skipped by the on_option_list_option_*
        # handlers' guard.
        self._ready = True
        self._select(self.sel_iter)

    def action_stop(self) -> None:
        self.run.request_stop()
        self.app.notify(f"stopping run {self.run.rid} …", timeout=4)
        self._render_now()

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
        labels = [story.iter_label(self.run, k) for k in range(self.run.cfg.iterations + 1)]
        sig = (tuple(labels), self.sel_iter)
        if sig == self._iters_sig:
            return
        self._iters_sig = sig
        ol.clear_options()
        for k, (markup, disabled) in enumerate(labels):
            text = Text.from_markup(markup)
            if k == self.sel_iter:
                # the highlighted row is dark-on-gold: coloured marks would vanish
                text = Text(text.plain, style="bold")
            ol.add_option(Option(text, id=f"it::{k}", disabled=disabled))
        ol.highlighted = self.sel_iter
        ol.scroll_to_highlight()

    # ---- rendering: Progress table --------------------------------------------
    def _score_signature(self) -> tuple:
        """What decides whether the Progress table needs a full rebuild
        (its row structure changed) rather than an in-place cell refill."""
        target = None if self.sel_iter == 0 else self.run.iter_target(self.sel_iter)
        return (tuple(self.run.identities()), target, self.sel_iter, self.run.reopened)

    def _refresh_score(self) -> None:
        """Rebuild the table when its rows change, else refill its cells."""
        if self._score_signature() != self._score_sig:
            self._rebuild_score()
        else:
            self._update_score()

    def _rebuild_score(self) -> None:
        """Rebuild the Progress rows: BEST OVERALL (multi-identity only), then
        identities grouped by role; ✎ marks what this iteration improves."""
        t = self.query_one("#idents", ClickTable)
        coord = t.cursor_coordinate
        t.clear()
        self._row_map = []
        self._row_keys = {}
        self._clickable = set()
        self._score_sig = self._score_signature()

        if self.run.reopened:
            # A run rebuilt from disk: per-identity Progress scores were never
            # persisted, so show an honest notice rather than the misleading
            # AutoAscend fallback incumbent()/best_overall() would return. The
            # iteration list and the Mutator Logs tab carry the real recovered
            # detail. No clickable cells -> no per-seed DetailView to open.
            # spread across the three columns so the message isn't truncated to
            # the identity column's width.
            t.add_row(Text.from_markup("[dim]— not recorded[/]"),
                      Text.from_markup("[dim]scores weren't saved[/]"),
                      Text.from_markup("[dim]see Mutator Logs ▸[/]"), key="norec")
            self._row_map.append(("norec", None))
            return

        target = None if self.sel_iter == 0 else self.run.iter_target(self.sel_iter)
        # I8: for a single-identity objective the harness never seeds/moves
        # the union cell (archive.py's `len(identities) > 1` guard) -- showing
        # it would freeze at the AutoAscend baseline forever, misleadingly
        # implying no progress while the one identity's own row improves. Omit
        # the row entirely; _row_map/_valid_cell/select routing are already
        # data-driven off _row_map, so simply not appending it is sufficient.
        if len(self.run.identities()) > 1:
            name = ("[b #ffd54a]✎ ★ BEST OVERALL[/]" if target == "union"
                    else "[b #d2a24c]★ BEST OVERALL[/]")
            t.add_row(Text.from_markup(name), "", "", key="ov")
            self._row_map.append(("overall", None))
        for role in self.run.roles_present():
            t.add_row(Text.from_markup(f"[b #d2a24c]{S.role_full(role)}[/]"), "", "",
                      key=f"role:{role}")
            self._row_map.append(("role", None))
            for ident in (i for i in self.run.identities() if self.run.role_of(i) == role):
                name = f"[b #ffd54a]✎ {ident}[/]" if ident == target else f"[b]  {ident}[/]"
                self._row_keys[ident] = t.add_row(Text.from_markup(name), "", "",
                                                  key=f"id:{ident}")
                self._row_map.append(("ident", ident))
        self._update_score()
        # keep the cursor on a program cell
        if coord is not None and self._valid_cell(coord.row, coord.column):
            t.move_cursor(row=coord.row, column=coord.column)
            self._last_cursor_row = coord.row
        else:
            first = next((r for r, (k, _) in enumerate(self._row_map) if k == "ident"), 0)
            t.move_cursor(row=first, column=1)
            self._last_cursor_row = first

    def _update_score(self) -> None:
        """Refill every program cell in place (cursor and focus kept) and
        recompute which cells are clickable."""
        if self.run.reopened:
            return
        t = self.query_one("#idents", ClickTable)
        k = self.sel_iter
        before_state = self.run.first_state_at is None
        clickable: set[tuple[int, int]] = set()
        for r, (kind, ident) in enumerate(self._row_map):
            best: float | None = None
            if kind == "overall":
                if before_state or (self.run.init_union is None
                                    and self.run.setup_ended_at is None):
                    best_txt = Text.from_markup("[#7c745f]scored when setup ends[/]")
                else:
                    bo = self.run.best_overall(k)
                    best_txt, best = S.best_cell(bo), bo[0]
                    clickable |= {(r, 0), (r, 1)}
                if k == 0 and best is not None:
                    # Ruling 16(c): at setup, once the union has scored, its
                    # "this iteration" cell opens the same program as "best
                    # so far" -- the only way to see it before iteration 1.
                    this, ok = "[#7c745f]open ▸[/]", True
                else:
                    this, ok = story.this_cell(self.run, None, k, best)
                key = "ov"
            elif kind == "ident" and ident is not None:
                if before_state:
                    best_txt = Text.from_markup("[#7c745f]fetching…[/]")
                else:
                    inc = self.run.incumbent(ident, k)
                    best_txt, best = S.best_cell(inc), inc[0]
                    clickable.add((r, 1))
                this, ok = story.this_cell(self.run, ident, k, best)
                key = f"id:{ident}"
            else:
                continue
            if ok:
                clickable.add((r, 2))
            t.update_cell(key, "best", best_txt, update_width=False)
            t.update_cell(key, "run", Text.from_markup(this), update_width=False)
        self._clickable = clickable

    # ---- rendering: Mutator Logs / Logs tabs ----------------------------------
    def _render_mutator(self) -> None:
        log = self.query_one("#mutlog", RichLog)
        log.clear()
        if self.sel_iter == 0:
            log.write(Text("setup doesn't run the agent — pick an iteration on the left",
                           style="dim"))
            self._mutlog_shown = 0
            return
        lines = self.run.logs.get(self.run.tag(self.sel_iter), [])
        for kind, text in lines:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._mutlog_shown = len(lines)

    # ---- the story: now line, Logs step list, status line ----------------------
    @property
    def steps_view(self) -> story.SectionView | None:
        """The step list the Logs tab shows right now."""
        return self._steps_view

    def _render_now(self) -> None:
        self.query_one("#now", Static).update(
            Text.from_markup(story.now_line(self.run, self.run.now())))

    def _render_steps(self) -> None:
        view = story.section_view(self.run, self.sel_iter, self.run.now())
        self._steps_view = view
        self.query_one("#steps", Static).update(_section_renderable(view))

    def _live_section(self) -> int | None:
        """What the run is working on now: 0 during setup, then the running
        iteration; None between iterations and once the run is over."""
        if not self.run.running or self.run.reopened:
            return None
        if self.run.setup_ended_at is None:
            return 0
        return self.run.running_iteration()

    def _last_ran(self) -> int:
        """The last iteration that started (0 if none did)."""
        ran = [k for k in range(1, self.run.cfg.iterations + 1)
               if k in self.run.iter_results or k in self.run.iter_times]
        return max(ran) if ran else 0

    def _render_statusline(self) -> None:
        live = self._live_section()
        live_name = _section_name(live) if live is not None and live != self.sel_iter else None
        improved = story.improved_count(self.run)
        self.query_one("#statusline", Static).update(S.status_line(
            _section_name(self.sel_iter), live_name, improved, self.run.cfg.iterations,
            self.run.run_time(), self.run.finished_usage()))

    def _select(self, index: int) -> None:
        self.sel_iter = index
        self._render_iters()
        # _render_iters may no-op (unchanged signature) even on the very first
        # real layout pass -- _backfill's _select runs after on_mount already
        # rendered the same labels/sel_iter pre-layout, when scroll_to_highlight
        # was a no-op -- so scroll explicitly here too (Ruling 3).
        self.query_one("#iters", OptionList).scroll_to_highlight()
        self._rebuild_score()
        self._render_mutator()
        self._render_steps()
        self._render_statusline()
        self._render_now()

    # ---- detail open/close -----------------------------------------------------
    def _open_detail(self) -> None:
        self.query_one("#main").display = False
        self.query_one("#detailview").display = True

    def close_detail(self) -> None:
        self.query_one("#detailview").display = False
        self.query_one("#main").display = True

    @property
    def detail_open(self) -> bool:
        return self.query_one("#detailview").display

    def open_best(self, ident: str) -> None:
        """Open the incumbent ("best so far") program for ``ident``: a run
        child (local source), a hub champion (re-run locally at init -- D3;
        source links to the GitHub reference), a seed cell -- no hub
        champion, played locally during setup (Ruling 11) -- or, only when
        the identity has no cell at all, the AutoAscend floor (D5 -- no
        per-seed table)."""
        score, label, kind, j = self.run.incumbent(ident, self.sel_iter)
        title = f" {ident} · {label} "
        dv = self.query_one("#detailview", DetailView)
        if kind == "run" and j is not None:
            ev = self.run.iteration_evals(j)[ident]
            src = (f"[dim]source[/]  [link=file:///runs/{self.run.rid}/iter{j}/{ident}/bot.py]"
                   f"bot.py ↗[/]")
            dv.show_eval(ev.rows, ev.total, title, src)
        elif kind == "hub":
            ev = self.run.iteration_evals(0)[ident]
            # its scored cell digest (post-eval) or, mid-cold-start, the pulled
            # champion's program_id from elite_of -- so the GitHub source link
            # resolves even before the champion's cell is scored.
            digest = ((self.run.init_cells.get(ident) or {}).get("digest")
                      or (self.run.elite_of().get(ident) or {}).get("program_id"))
            origin = (self.run.origins().get(digest) or {}) if digest else {}
            repo, sha = origin.get("repo"), origin.get("sha")
            src = (f"[dim]source[/]  [link=https://{repo}/commit/{sha}]{repo}@{sha} ↗[/]"
                   if repo and sha else "[dim]source[/]  [dim]origin unknown[/]")
            dv.show_eval(ev.rows, ev.total, title, src)
        elif kind == "aa" and ident in self.run.init_cells:
            # Ruling 11: a seed cell (--from-seed/--seed, no hub champion)
            # was measured on THIS machine during setup -- its games exist,
            # unlike the hub-reference AutoAscend floor show_baseline is for.
            ev = self.run.iteration_evals(0)[ident]
            src = "[dim]source[/]  [dim]the starting bot · played on your machine during setup[/]"
            dv.show_eval(ev.rows, ev.total, title, src)
        else:
            dv.show_baseline(title, score)
        self._open_detail()

    def open_run(self, ident: str) -> None:
        """Open this iteration's own candidate for ``ident`` -- always a local
        run child, live while the iteration is still evaluating."""
        ev = self.run.iteration_evals(self.sel_iter)[ident]
        title = f" {ident} · iter {self.sel_iter} "
        src = (f"[dim]source[/]  [link=file:///runs/{self.run.rid}/iter{self.sel_iter}/"
               f"{ident}/bot.py]bot.py ↗[/]")
        self.query_one("#detailview", DetailView).show_eval(
            ev.rows, ev.total, title, src, live_ref=(self.sel_iter, ident))
        self._open_detail()

    def open_program(self) -> None:
        """Open the BEST OVERALL (union cell) program's full seed x identity
        table."""
        info = self.run.best_overall(self.sel_iter)
        self.query_one("#detailview", DetailView).show_program(self.run, info)
        self._open_detail()

    def open_candidate(self) -> None:
        """This iteration's candidate across every identity -- the average
        the BEST OVERALL row's "this iteration" cell shows."""
        self.query_one("#detailview", DetailView).show_candidate(self.run, self.sel_iter)
        self._open_detail()

    def _refresh_detail_if_open(self) -> None:
        if self.detail_open:
            self.query_one("#detailview", DetailView).refresh_live()

    # ---- events ---------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.close_detail()

    def _pick(self, option_id: str) -> None:
        """The user picked a section: view it, and follow the live run only if
        that's the section it's working on."""
        if not self._ready or not option_id.startswith("it::"):
            return
        k = int(option_id.split("::")[1])
        if k == self.sel_iter:
            return
        self.following = k == self._live_section()
        self._select(k)

    def _stale(self, event: OptionList.OptionHighlighted | OptionList.OptionSelected) -> bool:
        """A change to ``OptionList.highlighted`` POSTS its event
        asynchronously (Textual's ``watch_highlighted``) -- if a second
        programmatic highlight change (another _render_iters()/_select(),
        e.g. from a follow-live re-render) lands before this one is
        dispatched, the event describes a moment that's no longer current.
        Acting on it would drag the view back to the old index and re-post
        the same event, thrashing forever (Ruling 15). The live OptionList
        object always reflects the LATEST assignment, so a mismatch here
        means a newer one has already superseded this event."""
        return event.option_index != event.option_list.highlighted

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        # populating #iters auto-highlights before #idents is ready -- _pick's
        # own _ready guard skips that, and _backfill's _select renders it.
        if self._stale(event):
            return
        self._pick(event.option.id or "")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._stale(event):
            return
        self._pick(event.option.id or "")

    def _follow_live(self) -> None:
        """While following: keep the view on what the run is doing; when the
        run ends, land on the last iteration that ran."""
        if not self.following:
            return
        target = self._live_section()
        if target is None and not self.run.running:
            target = self._last_ran()
        if target is not None and target != self.sel_iter:
            self._select(target)

    def _valid_cell(self, row: int, col: int) -> bool:
        return (row, col) in self._clickable

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        # Only program cells highlight: snap anywhere else to the nearest
        # clickable cell -- and never onto an unclickable one, so the snap
        # can't bounce between dead cells forever.
        if getattr(event.data_table, "id", None) != "idents":
            return
        row, col = event.coordinate.row, event.coordinate.column
        if self._valid_cell(row, col):
            self._last_cursor_row = row
            return
        n = len(self._row_map)
        going_up = row < self._last_cursor_row
        order = (list(range(row, -1, -1)) + list(range(row + 1, n))) if going_up \
            else (list(range(row, n)) + list(range(row - 1, -1, -1)))
        for r in order:
            for c in (col, 1, 2, 0):
                if self._valid_cell(r, c):
                    self._last_cursor_row = r
                    event.data_table.move_cursor(row=r, column=c)
                    return

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        # NOTE: a single click on a ClickTable posts CellSelected TWICE
        # (Textual 8.2.8 quirk) -- this is a pure function of the coordinate.
        row, col = event.coordinate.row, event.coordinate.column
        if not self._valid_cell(row, col) or row >= len(self._row_map):
            return
        kind, ident = self._row_map[row]
        if kind == "overall":
            # Ruling 16(c): at setup, col 2 is "open ▸" -- the same program
            # as "best so far", not the (nonexistent before iteration 1)
            # candidate.
            if col == 2 and self.sel_iter != 0:
                self.open_candidate()
            else:
                self.open_program()
        elif kind == "ident" and ident is not None:
            if col == 1:
                self.open_best(ident)
            elif col == 2:
                self.open_run(ident)

    # ---- live renders (forwarded by the app while this screen is on top) ------
    def render_state(self) -> None:
        if not self._ready:
            return   # pre-mount race (see __init__) -- on_mount will backfill
        self._follow_live()
        self._render_iters()
        self._refresh_score()
        self._render_steps()
        self._render_statusline()
        self._render_now()

    def render_episode(self, label: str, ep: dict) -> None:
        if not self._ready:
            return
        self._refresh_score()
        self._render_steps()
        self._render_now()
        self._refresh_detail_if_open()   # a live open_run() table gains a row

    def render_log(self, tag: str) -> None:
        if not self._ready:
            return
        self._render_now()               # the live action count
        if tag != self.run.tag(self.sel_iter):
            return   # not the viewed iteration -- its log isn't on screen
        log = self.query_one("#mutlog", RichLog)
        lines = self.run.logs.get(tag, [])
        for kind, text in lines[self._mutlog_shown:]:
            log.write(Text(text, style=_KIND_STYLE.get(kind, "")))
        self._mutlog_shown = len(lines)
        self._render_steps()             # the action count + last action

    def render_iteration(self, iteration: int, result: IterationResult) -> None:
        if not self._ready:
            return
        self._follow_live()
        self._render_iters()   # status rollover: running -> decided
        self._refresh_score()
        self._render_steps()
        self._render_statusline()
        self._render_now()
        # a still-open open_run() table upgrades from live (cause/time "—")
        # to the decided iteration's full per-seed detail (§5.5).
        self._refresh_detail_if_open()

    def _tick(self) -> None:
        # elapsed times and the pace move every second, between worker events
        if not self._ready:
            return
        self._follow_live()
        self._render_now()
        self._render_steps()
        self._render_statusline()
