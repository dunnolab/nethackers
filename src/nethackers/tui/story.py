"""Plain-language narration of an evolve ``Run`` for the monitor.

The always-visible "now" line, the Logs tab's step list (setup, then each
iteration), the iteration list's labels and the Progress table's "this
iteration" words -- pure functions of a ``Run`` and a ``now`` read from the
run's own clock, with no Textual, so every wording is unit-tested
(tests/test_tui_story.py). The approved look and wording live in
.superpowers/specs/2026-09-22-tui-ux-quirks/ (prototype + screenshots).

Honesty rules kept here: counts and measured times only; the one projection
("at this pace") comes from ``Run.pace_left``; outcome words are improved /
no gain / failed test / agent failed / error -- never "kept" or "discarded",
since the loop sends every scored bot to the hub.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from rich.markup import escape

from nethackers.harness.loop import IterationResult
from nethackers.tui import status as S
from nethackers.tui.run import Batch, Run, outcome_word

_WARN = "#d7a700"
RUN_MARK = f"[{S._FOCUS}]▶[/]"
DONE_MARK = f"[{S._GREEN}]✓[/]"
PEND_MARK = f"[{S._DIM}]·[/]"
FAIL_MARK = f"[{S._HP}]✗[/]"
NOGAIN_MARK = f"[{S._DIM}]✗[/]"
STOP_MARK = f"[{S._PARCHMENT}]■[/]"
WARN_MARK = f"[{_WARN}]⚠[/]"

LEGEND = (
    "score = average progression over the games (0 = never left level 1 · 1.0 = ascended)\n"
    "✎ being improved this iteration · ▲ beats the best so far · click a score for its games\n"
    "★ BEST OVERALL = the one bot with the best average across all your identities")

_SMOKE = "smoke test: one short game to check the edited bot runs"
_ANY_RULE = "keep it if it beats the best so far on any identity, or the best average"


@dataclass(frozen=True)
class StepRow:
    """One line of a step list: a one-cell mark, the label and a right-aligned
    duration ("" for none). Mark and label are Rich markup."""

    mark: str
    label: str
    dur: str = ""


@dataclass(frozen=True)
class SectionView:
    """What the Logs tab shows for one section: setup (0) or iteration k."""

    header: str
    rows: tuple[StepRow, ...] = ()
    intro: str | None = None
    footer: str | None = None


def clip(text: str, width: int) -> str:
    """The first line of ``text``, cut to ``width`` characters with an ellipsis."""
    line = text.splitlines()[0] if text else ""
    return line if len(line) <= width else line[: width - 1] + "…"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _dur(start: float | None, end: float | None) -> str:
    if start is None or end is None:
        return ""
    return S.ep_time(max(0.0, end - start))


def _avg(batch: Batch | None) -> float | None:
    rows = batch.rows() if batch is not None else []
    return mean(float(r["progress"]) for r in rows) if rows else None


def _avg_txt(avg: float | None) -> str:
    return f" · avg {avg:.2f}" if avg is not None else ""


def _decided(run: Run) -> dict[int, IterationResult]:
    """Decided iterations only -- the loop also reports a baseline as iteration 0."""
    return {k: r for k, r in run.iter_results.items() if k > 0}


def _improved_count(run: Run) -> int:
    return sum(1 for r in _decided(run).values() if r.registered)


def _actions_txt(n: int) -> str:
    return f"{n} {_plural(n, 'action', 'actions')}"


# ---- setup -------------------------------------------------------------------

@dataclass(frozen=True)
class _Group:
    key: str                  # the loop's batch key: program_id[:8] | "seed" | "union"
    name: str                 # what the step calls it
    idents: tuple[str, ...]


def _batch_label(key: str) -> str:
    return f"cold-start · dev [{key}]"   # harness/loop.py's cold-start batch labels


def _setup_groups(run: Run) -> list[_Group]:
    """The cold-start batches in the loop's order: one per distinct hub
    champion (grouped by program over the identities it owns), then the
    starting bot for identities with no champion, then -- only once it has
    started, since it depends on the board -- the best-on-average union."""
    if run.first_state_at is None:
        return []
    elite_of = run.elite_of()
    owned: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for ident, entry in elite_of.items():
        pid = str(entry.get("program_id") or "")
        owned.setdefault(pid[:8], []).append(ident)
        names[pid[:8]] = run.origin_label(pid)
    groups = [_Group(key, names[key], tuple(ids)) for key, ids in owned.items()]
    seed = tuple(i for i in run.identities() if i not in elite_of)
    if seed:
        groups.append(_Group("seed", "the starting bot", seed))
    if run.batch_for(_batch_label("union")) is not None:
        union = run.init_union
        name = (f"{run.origin_label(union['digest'])}, the best bot on average"
                if union else "the best bot on average")
        groups.append(_Group("union", name, tuple(run.identities())))
    return groups


def _group_total(run: Run, group: _Group, batch: Batch | None) -> int:
    if batch is not None and batch.total:
        return batch.total
    return len(group.idents) * run.games_per_identity()


def _group_who(group: _Group) -> str:
    if group.key == "union":
        return f"{escape(group.name)} on all {len(group.idents)} identities"
    return f"{escape(group.name)} on {', '.join(group.idents)}"


def _current_group(run: Run) -> tuple[_Group, Batch | None, float] | None:
    """The setup batch playing now -- (group, its batch, when it started) --
    or None when setup is over or between batches."""
    if run.setup_ended_at is not None or not run.running:
        return None
    start = run.first_state_at if run.first_state_at is not None else run.started
    for group in _setup_groups(run):
        batch = run.batch_for(_batch_label(group.key))
        if batch is not None and batch.done:
            start = batch.ended if batch.ended is not None else start
            continue
        return group, batch, start
    return None


def setup_view(run: Run, now: float) -> SectionView:
    n = len(run.identities())
    header = f"[b {S._AMBER}]Setup[/] — a fair starting score for each identity"
    if run.reopened:
        return SectionView(header=header, footer=(
            f"[{S._DIM}]setup wasn't recorded for runs from earlier sessions[/]"))
    intro = (f"[{S._DIM}]Before any edits, the current best bots are played on your "
             f"machine, so every later result is compared on the same games.[/]")
    rows: list[StepRow] = []
    first = run.first_state_at
    if first is None:
        what = ("preparing the starting bot" if run.cfg.from_seed
                else f"fetching the best bots for your {n} identities from the hub")
        rows.append(StepRow(RUN_MARK, what, S.ep_time(max(0.0, now - run.started))))
        return SectionView(header, tuple(rows), intro, _setup_next(run))
    groups = _setup_groups(run)
    champs = [g for g in groups if g.key not in ("seed", "union")]
    covered = sum(len(g.idents) for g in champs)
    if run.cfg.from_seed:
        fetched = "prepared the starting bot"
    elif not champs:
        fetched = f"checked the hub · no bots there yet for your {n} identities"
    else:
        bots = f"{len(champs)} {_plural(len(champs), 'bot covers', 'bots cover')}"
        whom = f"your {n} identities" if covered == n else f"{covered} of your {n} identities"
        fetched = f"fetched the best bots from the hub · {bots} {whom}"
    rows.append(StepRow(DONE_MARK, fetched, _dur(run.started, first)))
    start: float = first
    current = _current_group(run)
    for group in groups:
        batch = run.batch_for(_batch_label(group.key))
        total = _group_total(run, group, batch)
        who = _group_who(group)
        if batch is not None and batch.done:
            end = batch.ended if batch.ended is not None else now
            rows.append(StepRow(DONE_MARK, f"played {who} · {total} games{_avg_txt(_avg(batch))}",
                                _dur(start, end)))
            start = end
        elif current is not None and current[0] == group:
            played = len(batch.rows()) if batch is not None else 0
            rows.append(StepRow(
                RUN_MARK, f"playing {who} · [b]{played}/{total}[/] games{_avg_txt(_avg(batch))}",
                _dur(current[2], now)))
        else:
            rows.append(StepRow(PEND_MARK, f"[{S._DIM}]play {who} · {total} games[/]"))
    return SectionView(header, tuple(rows), intro, _setup_footer(run))


def _setup_next(run: Run) -> str:
    k = run.cfg.iterations
    return (f"[{S._DIM}]next: {k} {_plural(k, 'iteration', 'iterations')} — in each, the "
            f"agent edits one bot, we play it on the same games, and keep it if it's better[/]")


def _setup_footer(run: Run) -> str:
    if run.setup_ended_at is None:
        return _setup_next(run) if run.running else f"{FAIL_MARK} setup didn't finish"
    ids = run.identities()
    best = ""
    if len(ids) > 1:
        score, label = run.best_overall(1)[:2]
        best = f" · best overall [b]{score:.2f}[/] ({escape(label)})"
    elif ids:
        score, label = run.incumbent(ids[0], 1)[:2]
        best = f" · best so far [b]{score:.2f}[/] ({escape(label)})"
    took = S.ep_time(run.setup_duration() or 0.0)
    return f"{DONE_MARK} setup done in {took}{best} · the iterations start from here"


# ---- one iteration -------------------------------------------------------------

def _means(results: list[dict] | None) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in results or []:
        c = r.get("character")
        if c:
            by.setdefault(c, []).append(float(r.get("progress", 0.0)))
    return {c: mean(v) for c, v in by.items()}


def _keep_rule(run: Run, k: int) -> str:
    ids = run.identities()
    if len(ids) == 1:
        return f"keep it if its average beats {run.incumbent(ids[0], k)[0]:.2f}"
    return _ANY_RULE


def _killed(run: Run, k: int) -> bool:
    """Did Stop kill iteration k's agent? Authoritative once decided (the
    operator's stopped_reason); live, from when Stop was pressed."""
    res = run.iter_results.get(k)
    if res is not None and res.stopped_reason is not None:
        return res.stopped_reason == "killed"
    t = run.iter_times.get(k)
    at = run.stop_requested_at
    return (at is not None and t is not None and t.edit_start is not None
            and t.edit_start <= at and (t.edit_end is None or at <= t.edit_end))


def _iteration_header(run: Run, k: int, title: str) -> str:
    target = run.iter_target(k)
    if target == "union":
        score, label = run.best_overall(k)[:2]
        return (f"{title} — improving [b]BEST OVERALL[/], the best bot on average "
                f"[{S._DIM}](best {score:.2f} · {escape(label)})[/]")
    if target:
        score, label = run.incumbent(target, k)[:2]
        return (f"{title} — improving [b]{escape(target)}[/] "
                f"[{S._DIM}](best so far {score:.2f} · {escape(label)})[/]")
    return title


def _edit_rows(run: Run, k: int, now: float, rows: list[StepRow], reason: str) -> bool:
    """The agent's edit. False when the step list ends here."""
    t = run.iter_times.get(k)
    start = t.edit_start if t else None
    end = t.edit_end if t else None
    acts = run.actions(k)
    if reason.startswith("operator-error:"):
        rows.append(StepRow(
            FAIL_MARK, f"the agent failed: {escape(reason.split(':', 1)[1].strip())}",
            _dur(start, end)))
        return False
    if k not in run.iter_results and end is None:
        mark = RUN_MARK if run.running else STOP_MARK
        rows.append(StepRow(mark, f"{S.agent_name(run.cfg)} is editing the bot · "
                                  f"[b]{len(acts)}[/] {_plural(len(acts), 'action', 'actions')}"
                                  f" so far", _dur(start, now) if run.running else ""))
        if acts:
            rows.append(StepRow("", f"[{S._DIM}]last: {escape(clip(acts[-1], 60))} — full "
                                    f"transcript in [b]Mutator Logs[/][/]"))
        return run.running
    res = run.iter_results.get(k)
    spend = run.edit_usage(k).spend or (res.usage.spend if res and res.usage else 0)
    tok = f" · {S._compact(spend)} tokens" if spend else ""
    if _killed(run, k):
        rows.append(StepRow(STOP_MARK, f"the agent was stopped · {_actions_txt(len(acts))}{tok}",
                            _dur(start, end)))
    else:
        rows.append(StepRow(DONE_MARK, f"{S.agent_name(run.cfg)} edited the bot · "
                                       f"{_actions_txt(len(acts))}{tok}", _dur(start, end)))
    return True


def _smoke_rows(run: Run, k: int, now: float, rows: list[StepRow], reason: str) -> bool:
    """The smoke test. False when the step list ends here."""
    t = run.iter_times.get(k)
    edit_end = t.edit_end if t else None
    smoke_end = t.smoke_end if t else None
    if reason.startswith("gate:"):
        rows.append(StepRow(FAIL_MARK, f"smoke test failed — {escape(reason[5:].strip())}",
                            _dur(edit_end, smoke_end)))
        rows.append(StepRow("", f"[{S._DIM}]not played, not sent to the hub[/]"))
        return False
    decided = k in run.iter_results
    if not decided and edit_end is None:
        rows.append(StepRow(PEND_MARK, f"[{S._DIM}]{_SMOKE}[/]"))
    elif not decided and smoke_end is None:
        if not run.running:
            rows.append(StepRow(STOP_MARK, _SMOKE))
            return False
        rows.append(StepRow(RUN_MARK, _SMOKE, _dur(edit_end, now)))
    else:
        rows.append(StepRow(DONE_MARK, "smoke test passed", _dur(edit_end, smoke_end)))
    return True


def _play_row(run: Run, k: int, now: float, rows: list[StepRow]) -> None:
    t = run.iter_times.get(k)
    smoke_end = t.smoke_end if t else None
    play_end = t.play_end if t else None
    res = run.iter_results.get(k)
    if res is not None and (res.reason or "").startswith("error:") and res.results is None:
        return   # the iteration broke before any games were played
    batch = run.batch_for(f"{run.tag(k)} · dev")
    total = batch.total if batch is not None and batch.total else run.games_total()
    if res is None and smoke_end is None:
        rows.append(StepRow(PEND_MARK, f"[{S._DIM}]play the edited bot · {total} games "
                                       f"({run.games_per_identity()} per identity)[/]"))
    elif res is None and play_end is None:
        played = len(batch.rows()) if batch is not None else 0
        rows.append(StepRow(
            RUN_MARK if run.running else STOP_MARK,
            f"playing the edited bot · [b]{played}/{total}[/] games{_avg_txt(_avg(batch))}",
            _dur(smoke_end, now) if run.running else ""))
    else:
        fit = res.dev_fitness if res is not None and res.dev_fitness is not None else _avg(batch)
        rows.append(StepRow(DONE_MARK, f"played the edited bot · {total} games{_avg_txt(fit)}",
                            _dur(smoke_end, play_end)))


def _decision_row(run: Run, k: int, res: IterationResult) -> StepRow:
    if not res.registered:
        ids = run.identities()
        if len(ids) == 1 and res.dev_fitness is not None and not run.reopened:
            best = run.incumbent(ids[0], k)[0]
            return StepRow(NOGAIN_MARK, f"no gain — {res.dev_fitness:.2f} didn't beat {best:.2f}")
        return StepRow(NOGAIN_MARK, "no gain — it didn't beat the best so far on any "
                                    "identity, or the best average")
    means = _means(res.results)
    parts: list[str] = []
    for key in res.improved or []:
        name = "BEST OVERALL" if key == "union" else key
        new = res.dev_fitness if key == "union" else means.get(key)
        if new is None or run.reopened:
            parts.append(escape(name))
            continue
        old = run.best_overall(k)[0] if key == "union" else run.incumbent(key, k)[0]
        parts.append(f"{escape(name)} {new:.2f} [{S._DIM}](was {old:.2f})[/]")
    return StepRow(DONE_MARK, f"[{S._GREEN}]improved[/] " + " · ".join(parts))


def _hub_row(res: IterationResult) -> StepRow:
    if res.hub_reason:
        why = res.hub_reason.removeprefix("local-only: ")
        return StepRow(WARN_MARK, f"[{_WARN}]stayed local:[/] {escape(why)}")
    return StepRow(DONE_MARK, "sent to the hub")


def _decide_rows(run: Run, k: int, now: float, rows: list[StepRow], reason: str) -> None:
    res = run.iter_results.get(k)
    t = run.iter_times.get(k)
    play_end = t.play_end if t else None
    if reason.startswith("error:"):
        rows.append(StepRow(FAIL_MARK, f"the iteration hit an error: "
                                       f"{escape(reason.split(':', 1)[1].strip())}"))
        return
    if res is None:
        if play_end is None:
            rows.append(StepRow(PEND_MARK, f"[{S._DIM}]decide: {_keep_rule(run, k)}[/]"))
        elif run.running:
            rows.append(StepRow(RUN_MARK, "comparing with the best so far · sending to the hub…",
                                _dur(play_end, now)))
        return
    rows.append(_decision_row(run, k, res))
    rows.append(_hub_row(res))


def iteration_view(run: Run, k: int, now: float) -> SectionView:
    title = f"[b {S._AMBER}]Iteration {k} of {run.cfg.iterations}[/]"
    res = run.iter_results.get(k)
    t = run.iter_times.get(k)
    if res is None and (t is None or t.edit_start is None):
        why = ("not started yet" if run.running
               else "not run — the run stopped first" if run.status == "stopped"
               else "not run")
        return SectionView(
            header=f"{title} — {why}",
            intro=(f"[{S._DIM}]Each iteration: the agent edits one bot → a smoke test → "
                   f"{run.games_total()} games → kept if it's better.[/]"))
    rows: list[StepRow] = []
    reason = (res.reason if res is not None else "") or ""
    if _edit_rows(run, k, now, rows, reason) and _smoke_rows(run, k, now, rows, reason):
        _play_row(run, k, now, rows)
        _decide_rows(run, k, now, rows, reason)
    if run.reopened:
        footer: str | None = (f"[{S._DIM}]step timings weren't recorded for runs from "
                              f"earlier sessions[/]")
    elif res is None and not run.running:
        footer = f"{STOP_MARK} the run stopped during this iteration"
    else:
        footer = None
    return SectionView(_iteration_header(run, k, title), tuple(rows), footer=footer)


def section_view(run: Run, k: int, now: float) -> SectionView:
    """The Logs tab's content for section k (0 = setup)."""
    return setup_view(run, now) if k == 0 else iteration_view(run, k, now)


# ---- the now line ---------------------------------------------------------------

def _last_line(error: BaseException | None) -> str:
    lines = [ln.strip() for ln in str(error or "").splitlines() if ln.strip()]
    return lines[-1] if lines else type(error).__name__


def _aborted_after(run: Run) -> tuple[int, str] | None:
    """(failures, last detail) when agent failures in a row ended the run
    early; None otherwise."""
    decided = _decided(run)
    if run.status != "done" or len(decided) >= run.cfg.iterations:
        return None
    fails, detail = 0, ""
    for k in sorted(decided, reverse=True):
        reason = decided[k].reason or ""
        if not reason.startswith("operator-error:"):
            break
        if not fails:
            detail = reason.split(":", 1)[1].strip()
        fails += 1
    return (fails, detail) if fails else None


def _setup_now(run: Run, now: float, stopping: bool) -> str:
    current = _current_group(run)
    games = ""
    body = "Setup · finishing up…"
    if current is not None:
        group, batch, start = current
        played = len(batch.rows()) if batch is not None else 0
        games = f"[b]{played}/{_group_total(run, group, batch)}[/] games"
        if group.key == "union":
            what = f"playing the best bot on average on all {len(group.idents)} identities"
        elif group.key == "seed":
            m = len(group.idents)
            what = (f"playing the starting bot on the {m} "
                    f"{_plural(m, 'identity', 'identities')} with no hub champion")
        else:
            what = "playing the hub's best bots on your machine for a fair starting score"
        body = f"Setup · {what} · {games} · {S.ep_time(max(0.0, now - start))}"
    if stopping:
        return f"{STOP_MARK} Stopping once setup finishes" + (f" · {games}" if games else "")
    return f"{RUN_MARK} {body}"


def _iteration_now(run: Run, now: float, stopping: bool) -> str:
    total = run.cfg.iterations
    k = run.running_iteration()
    if k is None:
        return (f"{RUN_MARK} Iteration {len(_decided(run)) + 1} of {total} · "
                f"getting the next bot ready…")
    t = run.iter_times[k]
    batch = run.batch_for(f"{run.tag(k)} · dev")
    played = len(batch.rows()) if batch is not None and t.smoke_end is not None else 0
    games = batch.total if batch is not None and batch.total else run.games_total()
    if stopping:
        if t.edit_end is None:
            return (f"{STOP_MARK} Stopping · the agent is stopped; this iteration's smoke "
                    f"test and games still run, then the run ends")
        return f"{STOP_MARK} Stopping after this iteration's games · {played}/{games} games"
    head = f"Iteration [b]{k}[/] of {total}"
    if t.edit_end is None:
        target = run.iter_target(k)
        who = "BEST OVERALL" if target == "union" else escape(target or "the bot")
        body = (f"{head} · {S.agent_name(run.cfg)} is editing the bot for [b]{who}[/] · "
                f"[b]{len(run.actions(k))}[/] "
                f"{_plural(len(run.actions(k)), 'action', 'actions')} · "
                f"{_dur(t.edit_start, now)}")
    elif t.smoke_end is None:
        body = f"{head} · {_SMOKE} · {_dur(t.edit_end, now)}"
    elif t.play_end is None:
        avg = _avg(batch)
        avg_txt = f" · avg [b]{avg:.2f}[/]" if avg is not None else ""
        body = (f"{head} · playing the edited bot · [b]{played}/{games}[/] games{avg_txt}"
                f" · {_dur(t.smoke_end, now)}")
    else:
        body = f"{head} · comparing with the best so far · sending to the hub…"
    pace = run.pace_left(now)
    if pace is not None:
        body += f" [{S._DIM}]· at this pace ~{S.short_time(pace)} left[/]"
    return f"{RUN_MARK} {body}"


def now_line(run: Run, now: float) -> str:
    """The monitor's always-visible "what's happening now" line."""
    total = run.cfg.iterations
    improved = _improved_count(run)
    if run.reopened:
        return f"{DONE_MARK} Reopened from an earlier session · {improved} of {total} improved"
    if run.status == "failed":
        return f"{FAIL_MARK} Run failed: {escape(clip(_last_line(run.error), 100))}"
    if run.status == "stopped":
        return (f"{STOP_MARK} Stopped by you · {improved} of {total} improved · "
                f"{S.short_time(run.run_time())}")
    if run.status == "done":
        aborted = _aborted_after(run)
        if aborted is not None:
            fails, detail = aborted
            return (f"{FAIL_MARK} Stopped after {fails} failed agent runs in a row · "
                    f"last: {escape(clip(detail, 80))}")
        return (f"{DONE_MARK} Done · {total} {_plural(total, 'iteration', 'iterations')} · "
                f"{improved} improved · {S.short_time(run.run_time())}")
    stopping = run.stop.is_set()
    if run.first_state_at is None:
        n = len(run.identities())
        what = (f"Preparing the starting bot for your {n} identities…" if run.cfg.from_seed
                else f"Fetching the best bots for your {n} identities from the hub…")
        return f"{RUN_MARK} {what} · {S.ep_time(max(0.0, now - run.started))}"
    if run.setup_ended_at is None:
        return _setup_now(run, now, stopping)
    return _iteration_now(run, now, stopping)


# ---- the iteration list and the Progress cells -----------------------------------

def iter_label(run: Run, k: int) -> tuple[str, bool]:
    """(markup, disabled) for row k of the iteration list; row 0 is setup."""
    if k == 0:
        if run.reopened:
            return f"{DONE_MARK} [b]setup[/]", False
        if run.setup_ended_at is None:
            if run.running:
                return f"{RUN_MARK} [b]setup[/]   [{S._FOCUS}]live[/]", False
            word = "failed" if run.status == "failed" else "stopped"
            return f"{STOP_MARK} [b]setup[/]   [{S._DIM}]{word}[/]", False
        took = S.short_time(run.setup_duration() or 0.0)
        return f"{DONE_MARK} [b]setup[/]   [{S._DIM}]{took}[/]", False
    res = run.iter_results.get(k)
    if res is not None:
        word = outcome_word(res)
        secs = run.iteration_duration(k)
        dur = f"  [{S._DIM}]{S.short_time(secs)}[/]" if secs is not None else ""
        if word == "improved":
            return f"{DONE_MARK} [b]iter {k}[/]   [{S._GREEN}]improved[/]{dur}", False
        mark = NOGAIN_MARK if word == "no gain" else FAIL_MARK
        return f"{mark} [b]iter {k}[/]   [{S._DIM}]{word}[/]{dur}", False
    t = run.iter_times.get(k)
    if t is not None and t.edit_start is not None:
        if run.running:
            return f"{RUN_MARK} [b]iter {k}[/]   [{S._FOCUS}]live[/]", False
        return f"{STOP_MARK} [b]iter {k}[/]   [{S._DIM}]stopped[/]", False
    if not run.running:
        return f"[{S._DIM}]·  iter {k}   not run[/]", True
    return f"[{S._DIM}]·  iter {k}[/]", True


def this_cell(run: Run, ident: str | None, k: int, best: float | None) -> tuple[str, bool]:
    """Progress's "this iteration" cell while viewing section k -- for one
    identity, or across all of them (``ident`` None: the BEST OVERALL row) --
    and whether it's clickable (it is once games have results)."""
    dim = S._DIM
    if k == 0:
        return f"[{dim}]— setup doesn't edit[/]", False
    res = run.iter_results.get(k)
    t = run.iter_times.get(k)
    reason = (res.reason if res is not None else "") or ""
    if reason.startswith("gate:"):
        return f"[{S._HP}]—[/] [{dim}]failed smoke test[/]", False
    if reason.startswith("operator-error:"):
        return f"[{S._HP}]—[/] [{dim}]agent failed[/]", False
    if res is None:
        if t is None or t.edit_start is None:
            return f"[{dim}]—[/]", False
        if t.smoke_end is None:
            if not run.running:
                return f"[{dim}]— stopped[/]", False
            if t.edit_end is None:
                return f"[{dim}]waiting for the agent…[/]", False
            return f"[{dim}]smoke test…[/]", False
    evals = run.iteration_evals(k)
    views = list(evals.values()) if ident is None else [evals[ident]] if ident in evals else []
    rows = [row for view in views for row in view.rows]
    total = sum(view.total for view in views)
    if not rows:
        if reason.startswith("error:"):
            return f"[{S._HP}]—[/] [{dim}]error[/]", False
        return f"[{dim}]0/{total} games[/]", False
    avg = mean(float(row["progress"]) for row in rows)
    win = best is not None and avg > best
    color = S._GREEN if win else S._FOCUS
    tag = f"  [b {S._GREEN}]▲ new best[/]" if win else ""
    return f"[b {color}]{avg:.2f}[/]  [{dim}]{len(rows)}/{total} games[/]{tag}", True
