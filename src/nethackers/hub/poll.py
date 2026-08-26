"""Frozen answer vocabularies + validation for the Oracle poll
(GET /poll, POST /poll/vote). The single server-side source of truth for what
a vote's fields may contain; the client (web/index.html) owns the matching
labels, and tests/hub/test_poll.py asserts the two key sets agree."""

from __future__ import annotations

METHODS: frozenset[str] = frozenset({"programs", "rl", "llm", "symbolic", "hybrid", "never"})
TIMELINES: frozenset[str] = frozenset({"2027", "2030", "2035", "2040", "after", "never"})
ROLES: frozenset[str] = frozenset({"mlr", "player", "eng", "enth"})
XPS: frozenset[str] = frozenset({"never", "casual", "serious", "ascended"})

_MAX_VOTER_ID = 64


class PollValidationError(ValueError):
    """A vote field was missing or outside its frozen vocabulary."""


def clean_vote(
    *, voter_id: str, method: str, timeline: str, roles: list[str], xp: str | None
) -> dict:
    """Validate a raw vote; return the normalized fields to store. roles is
    deduped and sorted (so the stored JSON is canonical) and must be a subset
    of ROLES; xp may be None. Raises PollValidationError on any bad field."""
    if not isinstance(voter_id, str) or not (1 <= len(voter_id) <= _MAX_VOTER_ID):
        raise PollValidationError(f"voter_id must be 1..{_MAX_VOTER_ID} chars")
    if method not in METHODS:
        raise PollValidationError(f"unknown method: {method!r}")
    if timeline not in TIMELINES:
        raise PollValidationError(f"unknown timeline: {timeline!r}")
    if xp is not None and xp not in XPS:
        raise PollValidationError(f"unknown xp: {xp!r}")
    roles_norm = sorted(set(roles))
    unknown = [r for r in roles_norm if r not in ROLES]
    if unknown:
        raise PollValidationError(f"unknown role(s): {unknown}")
    return {
        "voter_id": voter_id, "method": method, "timeline": timeline,
        "roles": roles_norm, "xp": xp,
    }
