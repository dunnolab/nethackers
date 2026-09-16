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

_RESET_NOTE = (
    " The board was reset for arena major {major}: an earlier epoch's results "
    "are archived and are not comparable, because they were measured on mixed "
    "CPU architectures. Contributors re-register."
)


def _dash(value: Any) -> str:
    """A missing value renders as an em dash -- the discipline ``read_stats``
    already applies to ``last_registered_at``. Never a zero, never a guess."""
    return "—" if value is None else str(value)


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
    values = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "version": version or "unknown",
        "arena_major": str(ARENA_MAJOR),
        "programs": str(stats["programs"]),
        "hackers": str(stats["hackers"]),
        "ascensions": str(stats["ascensions"]),
        # read_stats returns 0.0 both for "no atoms" and for a genuine zero
        # progression. The two are indistinguishable here and a bare 0.0 reads
        # as a measurement, so an unmeasured board gets the dash.
        "best": _dash(stats["best"] or None),
        "last_registered": _dash(stats["last_registered_at"]),
        "public_floor": _dash(public_floor),
        "private_floor": (
            "" if private_floor is None
            else f", {private_floor} on Private Dungeons"
        ),
        "reset_note": (
            _RESET_NOTE.format(major=ARENA_MAJOR) if store.has_archive() else ""
        ),
    }
    text = _BRIEF.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
