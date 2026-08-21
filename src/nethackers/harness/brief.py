"""Assemble the (lean) operator prompt from the objective + parent scorecard."""
from __future__ import annotations

from collections import Counter

from nethackers.contracts.models import Evidence


def build_brief(
    objective_name: str,
    character: str,
    parent_evidence: Evidence,
    *,
    training_seeds: list[int] | None = None,
    wiki_path: str | None = None,
) -> str:
    ends = Counter(r.end_status or "unknown" for r in parent_evidence.results)
    tally = ", ".join(f"{end}×{n}" for end, n in ends.most_common())
    mean = parent_evidence.mean_progress

    seeds_note = ""
    if training_seeds:
        seeds_note = ": " + ", ".join(str(s) for s in training_seeds)

    if wiki_path is not None:
        have = (
            "**What you have.** Live Python + NLE; NetHack reference (offline): "
            f"{wiki_path}; the current bot is your starting point."
        )
    else:
        have = "**What you have.** Live Python + NLE; the current bot is your starting point."

    return (
        f"**Objective.** Improve this NetHack bot's **progression score** as {character} "
        "— the BALROG-style milestone metric the evaluator computes. Maximize *that*; "
        "depth/turn-count/survival matter only insofar as they raise it. **Don't game "
        "it:** no branching on seed fingerprints (initial glyphs/inventory) to replay a "
        "canned run, no exploiting scorer/NLE quirks — such candidates fail on held-out "
        "seeds and are rejected.\n\n"
        f"**Where it currently loses progression.** Mean {mean:.2f} over "
        f"{parent_evidence.episodes} games; outcomes: {tally}.\n\n"
        "**Make one focused change.** One well-reasoned, localized change per candidate; "
        "**leave a short comment at the edit stating the hypothesis** "
        "(`# hypothesis: …`) so the next iteration inherits your reasoning inline. Wide "
        "enough to co-adapt, but a cosmetic/no-op diff wastes an eval.\n\n"
        "**Seeds & the real test.** Develop against your training seeds "
        f"(provided{seeds_note}). Scored on **held-out seeds you'll never see** — "
        "generalize, don't memorize; testing on extra random seeds is a good "
        "self-check.\n\n"
        f"{have}\n\n"
        "**Before you finalize.** Must import cleanly, keep the `make_agent()` → "
        "`reset()`/`act()` contract, and not crash across a handful of seeds — else "
        "it scores zero."
    )
