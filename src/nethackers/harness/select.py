"""SELECT: resolve the parent to evolve from — the objective's top *trusted*
elite (hub is the index; the local content-addressed store is the byte-cache),
falling back to the cold-start seed. Pure + injectable (hub/store/fetch/rng)."""
from __future__ import annotations

import math
import random
import tempfile
from collections.abc import Callable
from pathlib import Path

from nethackers.harness.store import LocalTreeStore
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


def _sample(entries: list[dict], k: int, temperature: float,
            rng: random.Random) -> dict:
    top = sorted(entries, key=lambda e: e["score"], reverse=True)[:max(1, k)]
    if len(top) == 1 or k <= 1:
        return top[0]
    weights = [math.exp(e["score"] / temperature) for e in top]
    return rng.choices(top, weights=weights, k=1)[0]


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
    try:
        entries = list(hub.elites(objective))
    except Exception:
        return seed_tree, None
    trusted = [e for e in entries if _trusted(e, owner)]
    if not trusted:
        return seed_tree, None
    chosen = _sample(trusted, k, temperature, rng)
    digest = chosen["solution_digest"]
    if store.has(digest):
        return store.path(digest), digest
    with tempfile.TemporaryDirectory() as td:
        pulled = fetch(chosen, Path(td))
        if pulled is None:
            return seed_tree, None
        got = store.save(pulled)              # content-addressed -> integrity
    if got != digest or not store.has(digest):
        return seed_tree, None
    return store.path(digest), digest
