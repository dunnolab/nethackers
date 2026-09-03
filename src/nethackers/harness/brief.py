"""Assemble the operator prompt: a sectioned, human-readable brief that frames
the task in plain terms (identities, seeds), asks the mutator to raise the
overall average, and points it at /refs/. It never mentions the evolutionary
loop, iterations, or acceptance internals.
"""
from __future__ import annotations

INTRO = (
    "# You're improving a NetHack bot\n\n"
    "You are improving a Python program that plays **NetHack** through the "
    "**NetHack Learning Environment (NLE)**. Make **one** focused change that "
    "raises its score."
)

VOCABULARY = (
    "## Vocabulary\n"
    "- **identity** — one character the bot plays: role-race-alignment-gender, "
    "e.g. `val-hum-law-fem`.\n"
    "- **seed** — a fixed game RNG. One seed pins *everything* random — dungeon "
    "layout, monster and item generation, every roll — so the same seed always "
    "plays out the identical game. Not just a map: one concrete, fully-determined "
    "playthrough.\n"
    "- **score** — BALROG progression: starts near 0 and rises as the bot "
    "survives, descends, and advances. Higher is better.\n"
    "- **overall** — the mean score across all identities (every identity's "
    "seeds pooled).\n"
    "- **focused change** — one coherent idea (a single hypothesis), not "
    "necessarily a small edit. \"Focused\" means the *idea* is singular, not that "
    "the diff is small: carrying it out may take a large change — a refactor, a "
    "drastic rewrite, whatever the idea needs. One idea, any amount of code."
)

HOWTO = (
    "## How to make your change\n"
    "1. The bot lives at **`/workspace`** — edit the strategy code in the "
    "`autoascend/` package, not the `arena_adapter.py` glue.\n"
    "2. Make **ONE** focused change, marked with a `# hypothesis: …` comment "
    "saying what you expect it to improve.\n"
    "3. Keep the `make_agent()` → `reset()` / `act()` contract intact and make "
    "sure the code imports cleanly.\n"
    "4. **Don't game the score:** no branching on seed fingerprints, no "
    "exploiting scorer/NLE quirks — those don't generalize."
)

MEASURE = (
    "## How to measure (exactly like the judge)\n"
    "```\n"
    "python -m nethackers.arena.run --solution /workspace \\\n"
    "  --batch '[[0,\"<identity>\"], …]' --evaluation-id local --out /tmp/eval.json\n"
    "```\n"
    "- `--evaluation-id local` is the judge's seed namespace — any other id "
    "plays different, meaningless games.\n"
    "- Read per-seed results from the `--out` file.\n"
    "- **Run it as ONE foreground command and wait.** Give Bash a long timeout "
    "(up to 600000 ms) and evaluate a **small** sample of seeds so it finishes in "
    "that window. This sandbox is single-shot: do **not** background the eval "
    "(`run_in_background`), `sleep`-wait, or rely on task notifications — a "
    "backgrounded result is lost when your turn ends, and you'd choose your "
    "change blind."
)


def _scores(identities: list[str], per_identity: dict[str, float] | None,
            overall: float | None, target: float | None) -> str:
    pid = per_identity or {}
    full = bool(identities) and all(i in pid for i in identities)
    # weakest-first when every identity has a score, else the declared order
    order = sorted(identities, key=lambda i: pid[i]) if full else list(identities)
    lines = ["### Scores",
             "How this bot does on each character it plays — the low ones drag "
             "the average down:", "", "| character | score |", "| --- | --- |"]
    for i in order:
        cell = f"{pid[i]:.3f}" if i in pid else "—"
        lines.append(f"| `{i}` | {cell} |")
    lines.append("")
    if full and overall is not None and target is not None:
        lines.append(f"**Overall average now: {overall:.3f} · "
                     f"target to beat: {target:.3f}**")
    elif target is not None:
        lines.append(f"**target to beat: {target:.3f}**")
    return "\n".join(lines)


def _goal(n: int) -> str:
    return (
        "## Your goal\n"
        f"This one bot plays **{n} different characters** (identities). Raise its "
        "**overall average** across all of them — a better all-rounder, not a "
        "specialist. A change that lifts one character while dropping the others "
        "usually isn't a win; one that lifts the average is."
    )


def _whats_kept(target: float | None) -> str:
    bar = f"beats {target:.3f}" if target is not None else "goes up"
    return (
        "## What's kept\n"
        "Your edited bot is re-scored on the same fixed seeds. It's **kept** if "
        f"its **overall average {bar}**; a change that doesn't raise the average "
        "is discarded. So aim for changes that help across characters, not tricks "
        "that boost one and hurt the rest."
    )


def _per_seed(n_identities: int, seeds_per: int) -> str:
    total = n_identities * seeds_per
    return (
        "## Per-seed detail\n"
        "The table above averages over each identity's seeds. "
        f"**`/refs/parent-eval.json`** has one row per seed ({total} = "
        f"{n_identities} identities × {seeds_per} seeds): the `trajectory_id`, "
        "its `character` (identity), the `progress` score, the deepest "
        "`milestone`, and the `cause_of_death`. Read it to see which dungeons "
        "this bot does worst on and how it dies there."
    )


def _references() -> str:
    return (
        "## References\n"
        "You also have a read-only **`/refs/`** folder. Start with "
        "**`/refs/CONTEXT.md`** — it lists what's there: a pristine copy of this "
        "bot (`parent/`), its per-seed results (`parent-eval.json`), and other "
        "changes that were tried with the score each reached (`attempts.md`, with "
        "their code under `attempts/`)."
    )


def _seeds(training_seeds: list[int] | None, wiki_path: str | None) -> str:
    seeds = ""
    if training_seeds:
        lo, hi = min(training_seeds), max(training_seeds)
        if training_seeds == list(range(lo, hi + 1)):
            seeds = f" **{lo}–{hi}**"
        else:
            seeds = " " + ", ".join(str(s) for s in training_seeds)
    have = "You have live Python + NLE and the current bot as your starting point."
    if wiki_path:
        have = f"NetHack reference (offline): {wiki_path}. " + have
    return (
        "## Seeds\n"
        f"Your training seeds are{seeds}. Prefer **general** NetHack improvements "
        "over tricks tuned to these particular dungeons — they won't hold up. "
        f"{have}"
    )


def build_brief(
    objective_name: str,
    character: str,
    *,
    identities: list[str] | None = None,
    per_identity: dict[str, float] | None = None,
    overall: float | None = None,
    target: float | None = None,
    seeds_per_identity: int | None = None,
    training_seeds: list[int] | None = None,
    wiki_path: str | None = None,
) -> str:
    n_seeds = seeds_per_identity or (len(training_seeds) if training_seeds else 0)
    if identities and len(identities) > 1:
        parts = [
            INTRO, VOCABULARY, _goal(len(identities)),
            _scores(identities, per_identity, overall, target),
            _per_seed(len(identities), n_seeds),
            _whats_kept(target), _references(), HOWTO, MEASURE,
            _seeds(training_seeds, wiki_path),
        ]
    else:
        # single-identity objective: no average framing; show its own score.
        score = ""
        if per_identity and character in per_identity:
            score = f" It currently scores **{per_identity[character]:.3f}**."
        parts = [
            INTRO, VOCABULARY,
            f"## Your goal\nYou are improving this bot as **{character}**. Raise "
            f"its score.{score}",
            _per_seed(1, n_seeds), _references(), HOWTO, MEASURE,
            _seeds(training_seeds, wiki_path),
        ]
    return "\n\n".join(parts)
