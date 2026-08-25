"""SELECT: resolve the parent to evolve from — the objective's top *trusted*
elite (hub is the index; the local content-addressed store is the byte-cache),
falling back to the cold-start seed. Pure + injectable (hub/store/fetch/rng).

A *set* objective (role/list/glob token, resolved via
``nethackers.hub.selector.resolve``) selects instead from the
coverage-gated pool: only programs trusted-and-present in EVERY member
identity's elite board, scored by the mean of their per-identity scores.
Single/random/all objectives are unaffected -- unchanged ``hub.elites(name)``
path below."""
from __future__ import annotations

import math
import random
import tempfile
from collections.abc import Callable
from pathlib import Path
from statistics import mean

from nethackers.harness.store import LocalTreeStore
from nethackers.hub.selector import resolve
from nethackers.hubclient.pull import pull


def pull_fetch(entry: dict, dest: Path) -> Path | None:
    """Cache-miss resolver: git-pull the elite's {repo, commit_sha} pointer.
    Never fires for your own wins (they hit the cache); a synthetic/dead
    pointer just fails cleanly -> caller falls back to the seed."""
    try:
        return pull(f"{entry['repo']}@{entry['commit_sha']}", dest)
    except Exception:
        return None


def _trusted(entry: dict, owner: str) -> bool:
    # fixed policy: verified (by anyone) OR your own self-report.
    return entry.get("tier") == "verified" or entry.get("owner") == owner


def _set_identities(objective: str) -> tuple[str, ...] | None:
    """The member identities iff ``objective`` resolves to a set (role/list/
    glob); ``None`` for single/random/all/legacy-opaque strings, which take
    the existing single-objective path. Best-effort: an objective ``resolve``
    can't parse (e.g. the dummy tokens harness tests use) is just not a set."""
    try:
        r = resolve(objective)
    except ValueError:
        return None
    return r.identities if r.kind == "set" else None


def _coverage_gated_entries(hub, identities: tuple[str, ...], owner: str) -> list[dict]:
    """Trusted programs present in EVERY member identity's elite pool, scored
    by their mean per-identity score over S. Coverage-gated: a program missing
    any member is excluded. Any hub error on a member -> that member
    contributes nothing, which (correctly) empties the intersection."""
    per_member: list[dict[str, dict]] = []
    for ident in sorted(identities):
        try:
            entries = [e for e in hub.elites(ident) if _trusted(e, owner)]
        except Exception:
            entries = []
        per_member.append({e["solution_digest"]: e for e in entries})
    if not per_member or any(not m for m in per_member):
        return []
    common = set.intersection(*(set(m) for m in per_member))
    out = []
    for digest in common:
        scores = [m[digest]["score"] for m in per_member]
        base = dict(per_member[0][digest])
        base["score"] = mean(scores)  # union mean over S
        out.append(base)
    return out


def influence_pool(hub, identities: tuple[str, ...], owner: str) -> list[dict]:
    """Trusted elites from the UNION of every member identity's elite pool --
    the counterpart to ``_coverage_gated_entries``'s intersection, feeding
    coverage-aware influence sampling (a later brief) rather than the elites
    leaderboard. A program appears once per identity-column where it is a
    trusted elite, carrying that column's score; each entry is tagged with
    its source ``identity`` (so a later brief can say "strong at X").
    Coverage-weighting is emergent: a full-S generalist shows up in every
    column, a specialist in just its own. Any hub error on a member -> that
    member contributes nothing (mirrors ``_coverage_gated_entries``); never
    raises."""
    out: list[dict] = []
    for ident in sorted(identities):
        try:
            entries = [e for e in hub.elites(ident) if _trusted(e, owner)]
        except Exception:
            entries = []
        for e in entries:
            tagged = dict(e)
            tagged["identity"] = ident
            out.append(tagged)
    return out


def _sample(entries: list[dict], k: int, temperature: float,
            rng: random.Random) -> dict:
    top = sorted(entries, key=lambda e: e["score"], reverse=True)[:max(1, k)]
    if len(top) == 1 or k <= 1:
        return top[0]
    weights = [math.exp(e["score"] / temperature) for e in top]
    return rng.choices(top, weights=weights, k=1)[0]


def _resolve(entry: dict, store: LocalTreeStore,
             fetch: Callable[[dict, Path], Path | None]) -> tuple[Path, str] | None:
    """Serve an elite entry's bytes through the local cache: a hit resolves
    immediately (always true for your own wins); a miss pulls and caches.

    Integrity depends on the digest namespace. A **content** digest
    (``"sha256:<hex>"``) is verified content-addressed: ``store.save``
    recomputes it and the pulled bytes are used ONLY if they match. An **atom**
    identity (``"<repo>@<commit>"``, no colon) is verified by git -- the pull
    checked out exactly that commit -- so the bytes are cached under the atom
    key and trusted (a content-hash equality can't apply to a commit pointer).
    ``None`` on a miss + fetch-failure, or a content-digest mismatch."""
    digest = entry["solution_digest"]
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


def select_parent(
    hub, objective: str, store: LocalTreeStore, seed_tree: Path, *,
    owner: str, k: int = 1, temperature: float = 1.0,
    rng: random.Random | None = None,
    fetch: Callable[[dict, Path], Path | None] = pull_fetch,
) -> tuple[Path, str | None]:
    """Resolve the parent to evolve from. Returns ``(tree_path,
    elite_digest)`` -- ``elite_digest`` is ``None`` when the call fell back
    to the cold-start ``seed_tree`` (hub error, no trusted entries, a
    cache-miss fetch failure, or a digest mismatch on the pulled bytes).

    1. ``hub.elites(objective)`` -- any exception -> cold-start fallback.
       A *set* objective (role/list/glob) instead queries every member
       identity and coverage-gates: see ``_coverage_gated_entries``.
    2. Keep the *trusted* entries: ``tier == "verified"`` OR
       ``owner == <me>`` (fixed policy, no ``--trust`` knob). None trusted
       -> cold-start fallback.
    3. Choose among the top-``k`` trusted by score: ``k == 1`` is the
       deterministic argmax (old top-1 behavior); ``k > 1`` samples one
       with ``P ∝ exp(score / temperature)`` via the injected ``rng``.
    4. Serve the chosen digest's bytes through the local cache: a hit
       resolves immediately (always true for your own wins); a miss calls
       ``fetch(entry, tmp)`` and, on success, ``store.save`` recomputes the
       digest -- the result is used ONLY if it equals the hub's claimed
       digest (content-addressed integrity check). Any failure or mismatch
       -> cold-start fallback.
    """
    rng = rng or random.Random()
    identities = _set_identities(objective)
    if identities is not None:
        trusted = _coverage_gated_entries(hub, identities, owner)
        if not trusted:
            return seed_tree, None
        chosen = _sample(trusted, k, temperature, rng)
        resolved = _resolve(chosen, store, fetch)
        return resolved if resolved is not None else (seed_tree, None)
    try:
        entries = list(hub.elites(objective))
    except Exception:
        return seed_tree, None
    trusted = [e for e in entries if _trusted(e, owner)]
    if not trusted:
        return seed_tree, None
    chosen = _sample(trusted, k, temperature, rng)
    resolved = _resolve(chosen, store, fetch)
    return resolved if resolved is not None else (seed_tree, None)


def top_trusted_elite(
    hub, objective: str, store: LocalTreeStore, owner: str, *,
    fetch: Callable[[dict, Path], Path | None] = pull_fetch,
) -> tuple[dict, Path] | None:
    """The objective's single best TRUSTED elite, GREEDY (max score, never
    sampled) -- for mid-run migration (harness/loop.py). Returns ``(entry,
    tree_path)``, or ``None`` on a hub error, no trusted entries, or a
    cache-miss + fetch/integrity failure. Never raises.

    A *set* objective (role/list/glob) is coverage-gated the same way as
    ``select_parent``: greedy-max over the mean-scored intersection pool
    (see ``_coverage_gated_entries``) instead of a single ``hub.elites``
    call."""
    identities = _set_identities(objective)
    if identities is not None:
        trusted = _coverage_gated_entries(hub, identities, owner)
        if not trusted:
            return None
        top = max(trusted, key=lambda e: e["score"])
        resolved = _resolve(top, store, fetch)
        return (top, resolved[0]) if resolved is not None else None
    try:
        entries = list(hub.elites(objective))
    except Exception:
        return None
    trusted = [e for e in entries if _trusted(e, owner)]
    if not trusted:
        return None
    top = max(trusted, key=lambda e: e["score"])
    resolved = _resolve(top, store, fetch)
    return (top, resolved[0]) if resolved is not None else None
