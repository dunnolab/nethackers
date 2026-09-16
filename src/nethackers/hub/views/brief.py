"""The agent-facing brief: the markdown representation of ``GET /``.

Design 2026-09-16. A second representation of the same resource, not a
transcription of the page. It carries the three things the website cannot
give a fetcher: the live numbers (the page loads them with JS, so a fetcher
sees `Loading frontier keepers...`), the install commands (the page has
none), and the reset explanation (the page never mentions it -- four separate
agents read the resulting zeros as data loss).

Prose lives in ``web/brief.md`` beside ``index.html``, with
``{{placeholder}}`` slots filled here -- the same convention ``index.html``
already uses for ``{{version}}``. Every number is read from the store on the
request that serves it (I3); the template transcribes none of them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nethackers.arena_version import ARENA_MAJOR
from nethackers.hub.store import Store
from nethackers.hub.views.baseline import read_baseline
from nethackers.hub.views.source import VERIFIED, Epoch, VerificationUnavailable
from nethackers.hub.views.stats import read_stats

# The template shipped in the wheel package data, beside index.html.
_BRIEF = Path(__file__).parent.parent / "web" / "brief.md"

_RESET_EXPLANATION = (
    "The board was reset for arena major {major}: an earlier epoch's results "
    "are archived and are not comparable, because they were measured on mixed "
    "CPU architectures. {contributors}"
)


def _dash(value: Any) -> str:
    """A missing value renders as an em dash -- the discipline ``read_stats``
    already applies to ``last_registered_at``. Never a zero, never a guess."""
    return "—" if value is None else str(value)


def _reset_bullet(*, has_archive: bool, programs: int) -> str:
    """The lead bullet under "Read this first" (brief.md:8) -- the line an
    agent that truncates the document (the reader this ordering targets, per
    D6) is most likely to carry away alone. An unconditional claim here goes
    false the moment the board has anything registered: production reported
    programs=5 on 2026-09-16, twelve lines below where the old unconditional
    sentence sat. Three states, matching ``store.has_archive()`` and
    ``stats["programs"]``:

    - no archive: a hub that was never reset has nothing to explain here --
      the bullet is omitted outright, not rendered empty.
    - archive, zero programs: the reset just ran; the original claim holds.
    - archive, programs > 0: re-registration is under way, so the bullet must
      say something that stays true with rows on the board.

    Returns the full markdown list item (including the leading ``- ``), or
    ``""`` when the bullet should not appear at all.
    """
    if not has_archive:
        return ""
    if programs == 0:
        lead = "**Zero registered programs is the expected state, not a fault.**"
        contributors = "Contributors re-register."
    else:
        lead = "**The board is rebuilding, so the numbers are small.**"
        contributors = "Contributors are re-registering."
    explanation = _RESET_EXPLANATION.format(major=ARENA_MAJOR, contributors=contributors)
    return f"- {lead} {explanation}"


def render_brief(
    store: Store, *, epoch: Epoch | None = None, version: str = ""
) -> str:
    """The brief for this hub, right now."""
    stats = read_stats(store)
    public_floor = read_baseline(store, tier="self-reported")["overall"]
    try:
        private_floor = read_baseline(store, tier=VERIFIED, epoch=epoch)["overall"]
    except VerificationUnavailable:
        # A hub with no verifier still has a brief. Dropping the Private
        # Dungeons figure is the whole degradation; 503-ing the front page for
        # every markdown client is not an option.
        private_floor = None
    # read_stats returns 0.0 both for "no atoms" and for a genuine zero
    # progression. The two are indistinguishable here and a bare 0.0 reads as
    # a measurement, so an unmeasured board gets the dash. Rounded to 3 dp to
    # match the floor figures below, which per_identity_fold already rounds.
    raw_best = stats["best"] or None
    best = round(raw_best, 3) if raw_best is not None else None
    values = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": version or "unknown",
        "arena_major": str(ARENA_MAJOR),
        "programs": str(stats["programs"]),
        "hackers": str(stats["hackers"]),
        "ascensions": str(stats["ascensions"]),
        "best": _dash(best),
        "last_registered": _dash(stats["last_registered_at"]),
        "public_floor": _dash(public_floor),
        "private_floor": (
            "" if private_floor is None
            else f", {private_floor} on Private Dungeons"
        ),
        # I3: the brief transcribes no figures, so this clause is derived from
        # the live count rather than left as fixed prose -- the first
        # ascension would otherwise contradict the table three paragraphs up.
        "ascension_clause": (
            "; nothing has managed one yet" if stats["ascensions"] == 0 else ""
        ),
    }
    text = _BRIEF.read_text(encoding="utf-8")
    reset_bullet = _reset_bullet(has_archive=store.has_archive(), programs=stats["programs"])
    if reset_bullet:
        text = text.replace("{{reset_bullet}}", reset_bullet)
    else:
        # Drop the whole line, including its newline, so an un-reset hub is
        # left with no blank line where the bullet would have been.
        text = text.replace("{{reset_bullet}}\n", "")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
