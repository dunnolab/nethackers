"""Pure formatting of the evolve status bar's two lines. No Textual, no rich
widgets -- just strings, so it's unit-tested."""
from __future__ import annotations

from dataclasses import dataclass

from nethackers.tui.art import score_to_dlvl
from nethackers.tui.theme import glyph


@dataclass(frozen=True)
class EvolveConfig:
    objective: str
    backend: str
    iterations: int
    token_budget: int


def _compact(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(int(n))


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def format_status(
    cfg: EvolveConfig,
    state: dict,
    *,
    live_tokens: int = 0,
    eval_step: tuple[int, int, float] | None = None,
    elapsed_s: float = 0.0,
) -> tuple[str, str]:
    k, n = state["iteration"], cfg.iterations
    phase = state["phase"]

    if phase == "cold-start":
        line1 = "COLD START · scoring baseline …"
    elif phase == "mutating":
        line1 = (f"MUTATING iter {k}/{n} · "
                 f"{_compact(live_tokens)}/{_compact(cfg.token_budget)} tok · "
                 f"{_clock(elapsed_s)}")
    elif phase == "gating":
        line1 = f"GATING iter {k}/{n} · smoke …"
    elif phase in ("evaluating-dev", "evaluating-held"):
        split = "dev" if phase.endswith("dev") else "held"
        if eval_step is not None:
            i, total, mean = eval_step
            line1 = f"EVALUATING {split} · iter {k}/{n} · ep {i}/{total} · x̄ {mean:.3f}"
        else:
            line1 = f"EVALUATING {split} · iter {k}/{n} …"
    elif phase == "registered":
        line1 = f"✓ REGISTERED iter {k}/{n}"
    elif phase == "rejected":
        line1 = f"✗ iter {k}/{n} · {state['detail']}"
    elif phase == "error":
        line1 = f"✗ ERROR · {state['detail']}"
    elif phase == "done":
        line1 = f"DONE · {state['wins']}/{n} registered"
    else:
        line1 = phase

    wins = state["wins"]
    line2 = (f"best dev {state['best_dev']:.3f} (base {state['baseline_dev']:.3f}) · "
             f"held {state['best_held']:.3f} · {wins} win{'' if wins == 1 else 's'}")
    return line1, line2


# ---------------------------------------------------------------------------
# Monitor-screen formatters. These bind to the evolve-monitor screen's
# widgets (a later task) instead of the two-line status bar above. All pure
# strings -- no Textual, no rich widgets -- so they're unit-tested standalone.
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


def candidate_line(state: dict, *, live_tokens: int, token_budget: int,
                   elapsed_s: float) -> str:
    verb = _PHASE_VERB.get(state["phase"], state["phase"])
    return (f"{verb} ⠙  {_compact(live_tokens)}/{_compact(token_budget)} tok · "
            f"{_clock(elapsed_s)}")


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
