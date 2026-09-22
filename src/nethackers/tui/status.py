"""Pure formatting for the evolve-monitor screen (tui.screens.monitor.
RunMonitor): EvolveConfig plus the shared numeric/bar helpers and the
Progress-table/mutator-title/token-subline formatters it renders through. No
Textual, no rich widgets -- just strings/``Text``, so it's unit-tested."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text


@dataclass(frozen=True)
class EvolveConfig:
    objective: str
    backend: str
    iterations: int
    model: str | None = None
    effort: str | None = None
    operator_version: str | None = None
    from_seed: bool = False   # --from-seed: no hub fetch before setup


def _compact(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _bar(frac: float, width: int = 8) -> str:
    frac = max(0.0, min(1.0, frac))
    fill = round(frac * width)
    return "▓" * fill + "░" * (width - fill)


def _short(digest: str) -> str:
    """A short, DISTINGUISHING id for a champion or cell elite: the hex after
    a content digest's ``sha256:`` prefix (or the commit after an atom's
    ``@``), so cells seeded from the same tree aren't all rendered
    identically."""
    d = str(digest)
    if ":" in d:
        d = d.split(":", 1)[1]
    elif "@" in d:
        d = d.split("@", 1)[1]
    return d[:6] or "seed"


# ---------------------------------------------------------------------------
# Monitor-screen formatters (evolve-monitor rework, Task 8): the Progress
# table's cells, the mutator title, and the token/time status subline --
# what tui.screens.monitor.RunMonitor renders through.
# ---------------------------------------------------------------------------

_AMBER, _PARCHMENT, _DIM, _FOCUS, _GREEN, _GOLD, _HP = (
    "#d2a24c", "#d7c9a2", "#7c745f", "#ffd54a", "#00a000", "#c0a000", "#c04040")

ROLE_FULL = {"arc": "Archeologist", "bar": "Barbarian", "cav": "Caveman",
             "hea": "Healer", "kni": "Knight", "mon": "Monk", "pri": "Priest",
             "ran": "Ranger", "rog": "Rogue", "sam": "Samurai", "tou": "Tourist",
             "val": "Valkyrie", "wiz": "Wizard"}
_STATUS = {"ascended": ("★", _GOLD), "died": ("☠", _HP),
           "timed out": ("⧗", _DIM), "aborted": ("⊘", _DIM), "running": ("⊙", _DIM)}
_BACKEND_NAME = {"claude": "Claude Code", "codex": "Codex", "opencode2": "OpenCode"}


def dur(seconds: float) -> str:
    m = int(seconds // 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def ep_time(seconds: float) -> str:
    """Per-episode clock time: seconds under a minute, else Xm SSs."""
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def agent_name(cfg: EvolveConfig) -> str:
    """The coding agent's display name (``Claude Code``, ``Codex``, ``OpenCode``)."""
    return _BACKEND_NAME.get(cfg.backend, cfg.backend)


def short_time(seconds: float) -> str:
    """A section's or a run's duration: ``45s``, ``7m``, ``1h 04m``."""
    seconds = max(0.0, seconds)
    return ep_time(seconds) if seconds < 60 else dur(seconds)


def role_full(role: str) -> str:
    return ROLE_FULL.get(role, role)


def status_glyph(word: str) -> tuple[str, str]:
    return _STATUS.get(word, ("·", _DIM))


def best_cell(inc: tuple[float, str, str, int | None]) -> Text:
    score, label, kind, _ = inc
    color = {"hub": _AMBER, "run": _GREEN, "aa": _DIM}[kind]
    return Text.from_markup(f"[{color}]{label:<15}[/] [b {color}]{score:>4.2f}[/]")


def run_cell(avg: float | None, revealed: int, total: int, inc_score: float,
             is_init: bool, done: bool) -> Text:
    if is_init or avg is None or revealed == 0:
        return Text.from_markup("[dim]— · no mutation[/]" if is_init
                                else f"[dim]{'—':>4}  {revealed:>2}/{total} pending[/]")
    win = avg > inc_score
    color = _GREEN if win else _FOCUS
    spin = "" if done else " [dim]⊙[/]"
    tag = f"   [b {_GREEN}]▲ new best[/]" if win else ""
    return Text.from_markup(
        f"[b {color}]{avg:>4.2f}[/]  [dim]{revealed:>2}/{total}[/]{spin}{tag}")


def best_overall_cell(bo: tuple[float, str, str, int | None]) -> Text:
    score, label, kind, _ = bo
    color = {"aa": _DIM, "run": _GREEN, "hub": _PARCHMENT}[kind]
    return Text.from_markup(
        f"[b {_AMBER}]BEST OVERALL[/]  [b {color}]{label}[/]   [dim]open ▸[/]")


def mutator_title(cfg: EvolveConfig) -> Text:
    agent = agent_name(cfg)
    ver = f" [{_AMBER}]{cfg.operator_version}[/]" if cfg.operator_version else ""
    model = f"  [dim]· model[/] [b]{cfg.model}[/]" if cfg.model else ""
    effort = f"  [dim]· effort[/] [b]{cfg.effort}[/]" if cfg.effort else ""
    return Text.from_markup(f"[dim]mutator[/] [b {_PARCHMENT}]{agent}[/]{ver}{model}{effort}")


def token_subline(usage, seconds: float) -> Text:
    return Text.from_markup(
        f"[dim]tokens[/]  in [b]{_compact(usage.input)}[/] · out [b]{_compact(usage.output)}[/] "
        f"· cached [dim]([/]write [b]{_compact(usage.cache_creation)}[/] · "
        f"read [b]{_compact(usage.cache_read)}[/][dim])[/]      [dim]·[/]      "
        f"[dim]time[/] [b]{dur(seconds)}[/]")
