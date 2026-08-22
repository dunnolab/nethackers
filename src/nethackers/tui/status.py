"""Pure formatting for the evolve-monitor screen (tui.screens.evolve.
EvolveScreen): EvolveConfig plus the PARENT/CANDIDATE/eval/lineage/ledger/
status-line formatters it renders through. No Textual, no rich widgets --
just strings, so it's unit-tested."""
from __future__ import annotations

from dataclasses import dataclass

from nethackers.tui.art import score_to_dlvl
from nethackers.tui.theme import glyph


@dataclass(frozen=True)
class EvolveConfig:
    objective: str
    backend: str
    iterations: int
    model: str | None = None
    effort: str | None = None


def _compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# Monitor-screen formatters. These bind to the evolve-monitor screen's
# widgets (tui.screens.evolve.EvolveScreen) -- the old two-line
# format_status() status bar they replaced (EvolveApp's) was removed once
# EvolveApp itself was cut over to NetHackersApp/EvolveScreen (Task 10). All
# pure strings -- no Textual, no rich widgets -- so they're unit-tested
# standalone.
# ---------------------------------------------------------------------------


def _bar(frac: float, width: int = 8) -> str:
    frac = max(0.0, min(1.0, frac))
    fill = round(frac * width)
    return "▓" * fill + "░" * (width - fill)


def parent_panel(state: dict) -> str:
    d = str(state.get("parent_digest", ""))[:4]
    who = f"#{d}" if d else "seed"
    return (f"PARENT  {who} · gen {state['generation']} · "
            f"dev {state['parent_dev']:.2f}  held {state['parent_held']:.2f}")


_PHASE_VERB = {"mutating": "mutating", "gating": "gating",
               "evaluating-dev": "eval dev", "evaluating-held": "eval held"}


def candidate_line(state: dict, *, live_tokens: int, elapsed_s: float) -> str:
    verb = _PHASE_VERB.get(state["phase"], state["phase"])
    return f"{verb} ⠙  {_compact(live_tokens)} tok · {_clock(elapsed_s)}"


def eval_line(split: str, eval_step: tuple[int, int, float] | None,
              counts: dict[str, int]) -> str:
    if eval_step is None:
        return f"eval · {split}  ┄ pending"
    done, total, mean = eval_step
    tally = "  ".join(f"{glyph(k)}{v}" for k, v in counts.items() if v)
    return f"eval · {split}  {_bar(mean)}  x̄{mean:.2f}  {done}/{total}  {tally}".rstrip()


def lineage_strip(chain: list[str], *, best_dev: float, baseline_dev: float) -> str:
    nodes = " → ".join(n if n == "seed" else f"#{str(n)[:4]}" for n in chain)
    delta = best_dev - baseline_dev
    return f"lineage  {nodes} → ?     best dev {best_dev:.2f}  Δ{delta:+.2f}"


def iterations_ledger(rows: list[tuple[int, bool, str]]) -> str:
    def _one(k: int, ok: bool, reason: str) -> str:
        return f"{k} {'✓' if ok else '✗'} {reason}"
    return "   ".join(_one(*r) for r in rows) or "—"


def status_line(cfg: EvolveConfig, state: dict, *, live_tokens: int,
                elapsed_s: float) -> str:
    best = score_to_dlvl(state["best_dev"])
    return (f"{cfg.objective}  gen:{state['generation']}  "
            f"tok:{_compact(live_tokens)}  T:{_clock(elapsed_s)}  "
            f"best:{best}  w:{state['wins']}")


def scorecard(parent_means: dict[str, float],
              candidate_means: dict[str, float] | None,
              identities: list[str]) -> str:
    """A weakest-first, one-row-per-build block for generalist objectives:
    ``build  bar  x̄  Δ``, headed by the union mean, the weakest build
    (floor), and how many of the identities have a mean yet."""
    shown = candidate_means or parent_means
    present, total = len([i for i in identities if i in shown]), len(identities)
    umean = sum(shown.get(i, 0.0) for i in identities if i in shown) / max(1, present)
    order = sorted(identities, key=lambda i: shown.get(i, -1.0))
    present_ids = [i for i in identities if i in shown]
    flo = min(present_ids, key=lambda i: shown[i]) if present_ids else None
    header = f"builds · union x̄ {umean:.2f}"
    if flo is not None:
        header += f" · floor {flo} {shown[flo]:.2f} · coverage {present}/{total}"
    rows = []
    for i in order:
        val = shown.get(i)
        if val is None:
            rows.append(f"  {i:<18} —  (missing)")
            continue
        delta = ""
        if candidate_means and i in parent_means:
            delta = f"  Δ{candidate_means[i] - parent_means[i]:+.2f}"
        rows.append(f"  {i:<18} {_bar(val)} {val:.2f}{delta}")
    return header + "\n" + "\n".join(rows)
