"""Pure formatting of the evolve status bar's two lines. No Textual, no rich
widgets -- just strings, so it's unit-tested."""
from __future__ import annotations

from dataclasses import dataclass


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
    elif phase == "migrated":
        line1 = f"↥ MIGRATED iter {k}/{n} ← {state['detail']}"
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
