"""Assemble the (lean) operator prompt from the objective + parent scorecard."""
from __future__ import annotations

from collections import Counter

from nethackers.contracts.models import Evidence


def build_brief(objective_name: str, character: str, parent_evidence: Evidence) -> str:
    ends = Counter(r.end_status or "unknown" for r in parent_evidence.results)
    tally = ", ".join(f"{end}×{n}" for end, n in ends.most_common())
    mean = parent_evidence.mean_progress
    return (
        f"Improve this NetHack bot's progression as {character} "
        f"(objective '{objective_name}').\n\n"
        f"Current bot: mean progression {mean:.2f} over "
        f"{parent_evidence.episodes} games; outcomes: {tally}.\n\n"
        "Edit the bot's source in this working copy to make it reach deeper / "
        "survive longer for this character. Focus on the failure modes above.\n\n"
        "HARD CONSTRAINT: keep the entrypoint contract intact — bot.py must still "
        "define a top-level make_agent() returning an object with reset()/act(). "
        "Do not rename or remove it."
    )
