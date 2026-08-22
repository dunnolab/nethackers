"""Resolve an evolve/objective token to a subset S of the 73 identities.

Precedence: exact catalog identity | 'random' | 'all' | bare role | comma
list | fnmatch glob. Pure -- no I/O, no CATALOG mutation. `all` resolves to
every identity but is marked kind='all' so the evolve path can reject it with
a hint (use '*'); the hub board keeps its own 'all' handling untouched."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fnmatch import fnmatch

from nethackers.hub.objectives import IDENTITIES, ROLES

_IDENTITY_SET = frozenset(IDENTITIES)


@dataclass(frozen=True)
class ResolvedObjective:
    name: str
    identities: tuple[str, ...]
    kind: str  # "single" | "random" | "all" | "set"


def _set_name(members: tuple[str, ...], token: str) -> str:
    # A stable, human-ish name: the token verbatim if it's a role or glob,
    # else a content hash of the sorted members (arbitrary lists).
    if token in ROLES or "*" in token:
        return token
    digest = hashlib.sha1("\n".join(members).encode()).hexdigest()[:8]
    return f"set:{len(members)}:{digest}"


def resolve(token: str) -> ResolvedObjective:
    token = token.strip()
    if not token:
        raise ValueError("empty objective")
    if token == "random":
        return ResolvedObjective("random", (), "random")
    if token == "all":
        return ResolvedObjective("all", tuple(IDENTITIES), "all")
    if token in _IDENTITY_SET:
        return ResolvedObjective(token, (token,), "single")
    if token in ROLES:
        members = tuple(sorted(i for i in IDENTITIES if i.startswith(f"{token}-")))
        return ResolvedObjective(token, members, "set")
    if "," in token:
        members = tuple(sorted({p.strip() for p in token.split(",") if p.strip()}))
        unknown = [m for m in members if m not in _IDENTITY_SET]
        if unknown or not members:
            raise ValueError(f"unknown identities: {', '.join(unknown) or '(none given)'}")
        return ResolvedObjective(_set_name(members, token), members, "set")
    if "*" in token:
        members = tuple(sorted(i for i in IDENTITIES if fnmatch(i, token)))
        if not members:
            raise ValueError(f"glob {token!r} matched no identities")
        return ResolvedObjective(_set_name(members, token), members, "set")
    raise ValueError(f"unknown objective {token!r}")
