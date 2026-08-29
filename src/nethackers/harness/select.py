"""SELECT: serve an elite entry's bytes through the local content-addressed
cache (hub is the index; the local store is the byte-cache). Pure +
injectable (store/fetch).

``per_identity_elites`` is the MAP-Elites cold-start read: for each identity
in the objective's set, the top *trusted* elite (``owner == <me>``) plus its
resolved tree on disk."""
from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from nethackers.harness.store import LocalTreeStore
from nethackers.hubclient.pull import pull


def pull_fetch(entry: dict, dest: Path) -> Path | None:
    """Cache-miss resolver: git-pull the elite's ``reference: {repo, commit}``
    pointer. Never fires for your own wins (they hit the cache); a
    synthetic/dead pointer just fails cleanly -> caller falls back to the
    seed."""
    try:
        reference = entry["reference"]
        return pull(f"{reference['repo']}@{reference['commit']}", dest)
    except Exception:
        return None


def _trusted(entry: dict, owner: str) -> bool:
    # fixed policy: your own self-report only. (/elites' `tier` is always the
    # constant "self-reported" -- there is no "verified" tier to check.)
    return entry.get("owner") == owner


def _resolve(entry: dict, store: LocalTreeStore,
             fetch: Callable[[dict, Path], Path | None]) -> tuple[Path, str] | None:
    """Serve an elite entry's bytes through the local cache: a hit resolves
    immediately (always true for your own wins); a miss pulls and caches.

    Cached under ``entry["program_id"]`` -- opaque (``nethackers.hub.ids.
    program_id``, never a ``"sha256:<hex>"`` content digest), so this always
    takes the **atom** path: git verifies the pull (it checked out exactly
    the ``reference`` commit), and the bytes are cached under the id and
    trusted -- there is no content-hash namespace left to sniff for a
    ``store.save`` re-verify. ``None`` on a miss + fetch-failure.
    """
    digest = entry["program_id"]
    if store.has(digest):
        return store.path(digest), digest
    with tempfile.TemporaryDirectory() as td:
        pulled = fetch(entry, Path(td))
        if pulled is None:
            return None
        if ":" in digest:
            got = store.save(pulled)
            if got != digest or not store.has(digest):
                return None
        else:
            store.save_as(digest, pulled)   # atom identity: trust the pulled commit
    return store.path(digest), digest


def per_identity_elites(
    hub, identities: tuple[str, ...], owner: str, *,
    store: LocalTreeStore,
    fetch: Callable[[dict, Path], Path | None] = pull_fetch,
) -> dict[str, tuple[dict, Path]]:
    """For each identity in ``identities``, the top TRUSTED elite entry plus
    its resolved tree on disk -- the thin per-identity read the MAP-Elites cold
    start seeds cells from. An identity with no trusted elite (or whose top
    elite fails to resolve) is simply absent. Any hub error on a member ->
    that member absent (never raises)."""
    out: dict[str, tuple[dict, Path]] = {}
    for ident in identities:
        try:
            entries = [e for e in hub.elites(ident) if _trusted(e, owner)]
        except Exception:
            entries = []
        if not entries:
            continue
        top = max(entries, key=lambda e: e["score"])
        resolved = _resolve(top, store, fetch)
        if resolved is not None:
            out[ident] = (top, resolved[0])
    return out
