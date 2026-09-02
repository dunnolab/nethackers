"""Assemble the operator prompt: NetHack framing + a pointer to `/refs/`.

The brief no longer distills the parent's score/outcome history into text --
that (and the influence/attempt reference folders) is provisioned as real
files under `/refs/` (Task A2's `CONTEXT.md` + copied folders), which the
agent reads and analyzes itself.
"""
from __future__ import annotations

from nethackers.contracts.models import Evidence

NETHACK_PREAMBLE = (
    "You are improving a program that plays **NetHack** (via NLE). It is scored by a "
    "BALROG-style **progression** metric — starts near 0 and rises as the bot survives "
    "and descends/advances (the evaluator defines the milestones). Maximize that.\n\n"
    "**The current bot is at `/workspace`** — edit it. **Strong reference solutions and "
    "recent rejected attempts are under `/refs/` — read `/refs/CONTEXT.md` first**, analyze "
    "them (diff, read, or run `python -m nethackers.arena.run` yourself), then make ONE "
    "focused change with a `# hypothesis: …` comment at the edit.\n\n"
    "Don't game it: no branching on seed fingerprints, no exploiting scorer/NLE quirks — "
    "such candidates won't generalize. Keep the `make_agent()` → `reset()`/`act()` "
    "contract and import cleanly.\n\n"
    "**Measure like the judge.** Evaluate exactly as the judge does: "
    "`python -m nethackers.arena.run --solution /workspace "
    "--batch '[[0,\"<build>\"], …]' --evaluation-id local --out /tmp/eval.json` "
    "— pass `--evaluation-id local` (the judge's seed namespace; any other id plays "
    "different, meaningless games) and read the per-episode results from the `/tmp/eval.json` "
    "`--out` file. **Run that eval as ONE foreground command and wait for it to finish** — give "
    "Bash a long timeout (up to 600000 ms) and evaluate a SMALL sample of seeds so it completes "
    "synchronously in that window. This sandbox is single-shot: do NOT background the eval "
    "(`run_in_background`), `sleep`-wait for it, or rely on task notifications — a backgrounded "
    "eval's result is lost when your turn ends, leaving you to decide the change blind. "
    "Put your edits in the strategy code (the `autoascend/` package), not the "
    "`arena_adapter.py` glue."
)


def _set_block(identities: list[str], per_identity: dict[str, float] | None,
               sampled_cell: str | None = None, cell_score: float | None = None,
               union_score: float | None = None) -> str:
    n = len(identities)
    lines = [f"**Objective — one bot, {n} character builds.** You're improving a single "
             f"bot scored per-build AND on its overall average across the {n} builds (the "
             f"'union'). A win on EITHER a single build or the union counts."]
    if sampled_cell == "union":
        base = f"{cell_score:.3f}" if cell_score is not None else "the best so far"
        lines.append(f"**This program is the current best OVERALL** (union {base}). Make ONE "
                     "focused change that raises the overall union above its current best.")
    elif sampled_cell:
        base = f" ({cell_score:.3f})" if cell_score is not None else ""
        lines.append(f"**This program is the current best on `{sampled_cell}`**{base}. Make ONE "
                     f"focused change that pushes `{sampled_cell}` above its current best **or** "
                     "raises the overall union — target a concrete build you can actually beat, "
                     "not 'the average' in the abstract.")
    if per_identity:
        ordered = sorted(identities, key=lambda i: per_identity.get(i, 0.0))
        table = "  ".join(f"{i} {per_identity[i]:.2f}" for i in ordered if i in per_identity)
        u = f"  ·  union {union_score:.2f}" if union_score is not None else ""
        lines.append(f"**Scoreboard now.** {table}{u}")
    else:
        lines.append("Builds: " + ", ".join(identities))
    return "\n\n".join(lines)


def _attempts_block(attempts: list[str] | None) -> str:
    """A run-global anti-repeat list: one line per earlier attempt this run
    (its id + the change it made + its outcome). Surfaced verbatim so the
    mutator can avoid re-deriving a mutation that already failed. Empty/absent
    -> no block at all (the first iteration has no history)."""
    if not attempts:
        return ""
    lines = "\n".join(f"- {a}" for a in attempts)
    return (
        "**Earlier attempts this run — don't just repeat these.** These changes "
        "were already tried this run; build on them or go elsewhere, don't rediscover "
        f"a dead end:\n{lines}"
    )


def build_brief(
    objective_name: str,
    character: str,
    parent_evidence: Evidence | None,
    *,
    training_seeds: list[int] | None = None,
    wiki_path: str | None = None,
    identities: list[str] | None = None,
    per_identity: dict[str, float] | None = None,
    attempts: list[str] | None = None,
    sampled_cell: str | None = None,
    cell_score: float | None = None,
    union_score: float | None = None,
) -> str:
    # parent_evidence is kept for caller/signature stability but is no longer
    # distilled into text -- its score/outcome detail lives in
    # /refs/CONTEXT.md (Task A2), which the agent reads directly.
    del parent_evidence

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

    if identities and len(identities) > 1:
        body = _set_block(identities, per_identity, sampled_cell, cell_score, union_score)
    else:
        body = f"You are improving it as **{character}** (objective '{objective_name}')."

    tail = (
        "**Seeds.** Develop against your training seeds"
        f"{seeds_note}. You're graded on these fixed public dungeon seeds — prefer "
        "**general** NetHack improvements over ones tuned to these particular dungeons, "
        "which won't hold up.\n\n"
        f"{have}"
    )

    attempts_block = _attempts_block(attempts)
    parts = [NETHACK_PREAMBLE, body]
    if attempts_block:
        parts.append(attempts_block)
    parts.append(tail)
    return "\n\n".join(parts)
