"""Pure helpers to extract the NetHack cause-of-death from an xlogfile.

NetHack (compiled with XLOGFILE, as in nle==1.3.0) appends one tab-separated
`key=value` record per game end; the `death=` field is the verbatim killer
string ("killed by a jackal", "starved to death", "petrified by a cockatrice").
These helpers isolate the parsing/filtering so it is testable without NLE.
"""
from __future__ import annotations

import os
from pathlib import Path

# `death=` values whose first word means "not a real death" (a win, a give-up,
# or an NLE-forced quit on truncation) -- excluded from cause-of-death stats.
_NON_DEATH_PREFIXES = {"quit", "escaped", "ascended"}


def parse_death_cause(xlog_text: str, *, is_ascended: bool) -> str | None:
    """The verbatim `death=` string of the last record, or None for a
    non-death (ascension / quit / escaped) or when no `death=` field exists."""
    if is_ascended:
        return None
    lines = [line for line in xlog_text.splitlines() if line.strip()]
    if not lines:
        return None
    fields = dict(
        part.split("=", 1) for part in lines[-1].split("\t") if "=" in part
    )
    raw = fields.get("death", "").strip()
    if not raw or raw.split()[0] in _NON_DEATH_PREFIXES:
        return None
    return raw


def read_death_cause(
    vardir: str | os.PathLike[str] | None, *, is_ascended: bool
) -> str | None:
    """Read `{vardir}/xlogfile` and return its cause of death, degrading to
    None on any missing/unreadable input (the NLE temp dir may be gone)."""
    if not vardir:
        return None
    try:
        text = Path(vardir, "xlogfile").read_text()
    except OSError:
        return None
    return parse_death_cause(text, is_ascended=is_ascended)
