# tests/test_harness_select.py
import random
from pathlib import Path

from nethackers.harness.select import (
    influence_pool,
    sample_seeds,
    select_parent,
    top_trusted_elite,
)
from nethackers.harness.store import LocalTreeStore


class _Hub:
    def __init__(self, entries): self._e = entries
    def elites(self, objective): return list(self._e)


def _tree(tmp, name, text="x"):
    d = tmp / name
    d.mkdir()
    (d / "bot.py").write_text(text)
    return d


def test_cache_hit_returns_top_trusted(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    win = _tree(tmp_path, "win", "win-code")
    digest = store.save(win)                     # your own win is already cached
    hub = _Hub([{"solution_digest": digest, "score": 0.2, "owner": "me",
                 "tier": "self-reported", "repo": "r", "commit_sha": "c"}])
    seed = _tree(tmp_path, "seed")
    got, chosen = select_parent(hub, "obj", store, seed, owner="me")
    assert chosen == digest and got == store.path(digest)


def test_trust_filter_drops_stranger_self_report(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    hub = _Hub([{"solution_digest": "sha256:x", "score": 0.9, "owner": "stranger",
                 "tier": "self-reported", "repo": "r", "commit_sha": "c"}])
    seed = _tree(tmp_path, "seed")
    got, chosen = select_parent(hub, "obj", store, seed, owner="me")
    assert chosen is None and got == seed        # stranger's 0.9 ignored → cold start


def test_verified_stranger_is_trusted(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    d = store.save(_tree(tmp_path, "v", "verified-code"))
    hub = _Hub([{"solution_digest": d, "score": 0.1, "owner": "stranger",
                 "tier": "verified", "repo": "r", "commit_sha": "c"}])
    got, chosen = select_parent(hub, "obj", store, _tree(tmp_path, "seed"), owner="me")
    assert chosen == d


def test_hub_error_falls_back_to_seed(tmp_path):
    class _Boom:
        def elites(self, o): raise RuntimeError("down")
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    got, chosen = select_parent(_Boom(), "obj", store, seed, owner="me")
    assert chosen is None and got == seed


def test_cache_miss_fetches_and_verifies_digest(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    real = _tree(tmp_path, "real", "real-code")
    real_digest = LocalTreeStore(tmp_path / "probe").save(real)   # compute its digest
    def fetch(entry, dest):                       # simulate pulling the real tree
        (Path(dest) / "bot.py").write_text("real-code")
        return Path(dest)
    hub = _Hub([{"solution_digest": real_digest, "score": 0.3, "owner": "stranger",
                 "tier": "verified", "repo": "r", "commit_sha": "c"}])
    got, chosen = select_parent(hub, "obj", store, _tree(tmp_path, "seed"),
                                owner="me", fetch=fetch)
    assert chosen == real_digest and store.has(real_digest)


def test_cache_miss_digest_mismatch_falls_back(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    def fetch(entry, dest):
        (Path(dest) / "bot.py").write_text("WRONG-content")
        return Path(dest)
    hub = _Hub([{"solution_digest": "sha256:claimed", "score": 0.3, "owner": "me",
                 "tier": "self-reported", "repo": "r", "commit_sha": "c"}])
    seed = _tree(tmp_path, "seed")
    got, chosen = select_parent(hub, "obj", store, seed, owner="me", fetch=fetch)
    assert chosen is None and got == seed         # pulled bytes != claimed digest


def test_resolve_atom_identity_trusts_pulled_commit(tmp_path):
    # Hub entries are keyed by repo@commit (no colon): the commit sha IS the
    # content identity (git-verified by the checkout on pull), so pulled bytes
    # are cached under it and adopted -- NOT rejected by a content-hash equality
    # that can't apply (regression: this path raised IndexError in the store).
    store = LocalTreeStore(tmp_path / "store")
    atom = "github.com/o/r@" + "a" * 40
    def fetch(entry, dest):
        (Path(dest) / "bot.py").write_text("pulled-code")
        return Path(dest)
    hub = _Hub([{"solution_digest": atom, "score": 0.5, "owner": "me",
                 "tier": "self-reported", "repo": "github.com/o/r", "commit_sha": "a" * 40}])
    got, chosen = select_parent(hub, "obj", store, _tree(tmp_path, "seed"),
                                owner="me", fetch=fetch)
    assert chosen == atom and got == store.path(atom) and store.has(atom)


def test_top_trusted_elite_adopts_atom_identity(tmp_path):
    # migration must adopt a trusted repo@commit elite (the common atom case),
    # not crash on it -- the bug that stalled every evolve iteration.
    store = LocalTreeStore(tmp_path / "store")
    atom = "github.com/o/r@" + "b" * 40
    def fetch(entry, dest):
        (Path(dest) / "bot.py").write_text("x")
        return Path(dest)
    hub = _Hub([{"solution_digest": atom, "score": 0.7, "owner": "me",
                 "tier": "self-reported", "repo": "github.com/o/r", "commit_sha": "b" * 40}])
    entry, path = top_trusted_elite(hub, "obj", store, "me", fetch=fetch)
    assert entry["solution_digest"] == atom and path == store.path(atom)


def test_topk_sampling_stays_in_topk_and_is_seeded(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    ds = [store.save(_tree(tmp_path, f"t{i}", f"code{i}")) for i in range(3)]
    entries = [{"solution_digest": ds[i], "score": 0.3 - 0.1 * i, "owner": "me",
                "tier": "self-reported", "repo": "r", "commit_sha": "c"} for i in range(3)]
    hub = _Hub(entries)
    seed = _tree(tmp_path, "seed")
    chosen = {select_parent(hub, "o", store, seed, owner="me", k=3,
                            rng=random.Random(s))[1] for s in range(30)}
    assert chosen <= set(ds[:3]) and len(chosen) > 1          # samples, stays in top-k
    # k=1 is deterministic argmax:
    assert select_parent(hub, "o", store, seed, owner="me", k=1)[1] == ds[0]


def test_top_trusted_elite_is_greedy_max_score(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    lo = store.save(_tree(tmp_path, "lo", "lo"))
    hi = store.save(_tree(tmp_path, "hi", "hi"))
    hub = _Hub([
        {"solution_digest": lo, "score": 0.2, "owner": "me", "tier": "self-reported",
         "repo": "r", "commit_sha": "c"},
        {"solution_digest": hi, "score": 0.7, "owner": "me", "tier": "self-reported",
         "repo": "r", "commit_sha": "c"},
    ])
    entry, path = top_trusted_elite(hub, "obj", store, "me")
    assert entry["solution_digest"] == hi and path == store.path(hi)  # greedy top, not lo


def test_top_trusted_elite_applies_trust_filter(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    d = store.save(_tree(tmp_path, "s", "s"))
    hub = _Hub([{"solution_digest": d, "score": 0.9, "owner": "stranger",
                 "tier": "self-reported", "repo": "r", "commit_sha": "c"}])
    assert top_trusted_elite(hub, "obj", store, "me") is None  # stranger self-report dropped


def test_top_trusted_elite_none_on_hub_error(tmp_path):
    class _Boom:
        def elites(self, o): raise RuntimeError("down")
    assert top_trusted_elite(_Boom(), "obj", LocalTreeStore(tmp_path / "s"), "me") is None


def test_top_trusted_elite_none_on_resolve_failure(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    def fetch(entry, dest):
        (Path(dest) / "bot.py").write_text("WRONG")   # != claimed digest
        return Path(dest)
    hub = _Hub([{"solution_digest": "sha256:claimed", "score": 0.5, "owner": "me",
                 "tier": "self-reported", "repo": "r", "commit_sha": "c"}])
    assert top_trusted_elite(hub, "obj", store, "me", fetch=fetch) is None


# -- coverage-gated selection over an identity set (Task 7) -----------------
# "wiz-elf-cha-mal" / "wiz-orc-cha-mal" are real IDENTITIES entries, so
# `resolve("wiz-elf-cha-mal,wiz-orc-cha-mal")` yields kind="set".

class _HubByIdentity:
    """Per-identity elite pools -- unlike `_Hub`, `.elites(x)` depends on x."""
    def __init__(self, pools):
        self.pools = pools  # {identity: [entry, ...]}
        self.calls = []
    def elites(self, objective):
        self.calls.append(objective)
        return list(self.pools.get(objective, []))


def _entry(digest, score, owner="dev", tier="verified"):
    return {"solution_digest": digest, "score": score, "owner": owner, "tier": tier,
            "repo": "r", "commit_sha": "c"}


def test_set_select_resolves_cached_covered_entry(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    digest = store.save(_tree(tmp_path, "p1", "p1-code"))  # already cached, e.g. our own win
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry(digest, 0.6)],
        "wiz-orc-cha-mal": [_entry(digest, 0.4)],
    })
    seed = _tree(tmp_path, "seed")
    tree, chosen = select_parent(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, seed, owner="dev")
    assert chosen == digest and tree == store.path(digest)


def test_set_select_scores_by_mean_and_excludes_partial_coverage(tmp_path):
    # P1 covers both identities (mean of .6/.4 = .5); P2 covers only one, even
    # with a higher raw score (.9) there -> excluded, never chosen.
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry("sha256:P1", 0.6), _entry("sha256:P2", 0.9)],
        "wiz-orc-cha-mal": [_entry("sha256:P1", 0.4)],
    })
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    seen = {}
    def spy(entry, dest):
        seen["digest"] = entry["solution_digest"]
        seen["score"] = entry["score"]
        return None  # force cache-miss + fetch-failure: asserts the CHOICE, not the bytes
    tree, chosen = select_parent(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, seed,
                                 owner="dev", fetch=spy)
    assert seen["digest"] == "sha256:P1" and seen["score"] == 0.5
    assert tree == seed and chosen is None  # unresolvable here -> cold-start fallback


def test_set_select_empty_intersection_falls_back_to_seed(tmp_path):
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry("sha256:P2", 0.9)],
        "wiz-orc-cha-mal": [],
    })
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    tree, chosen = select_parent(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, seed, owner="dev")
    assert tree == seed and chosen is None
    # queried per-identity (proves the set was actually decomposed, not just
    # treated as one opaque unknown-objective key that happens to fall back too)
    assert hub.calls == ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]


def test_set_select_drops_untrusted_entries_before_intersecting(tmp_path):
    # Same digest in both raw pools, but a stranger's self-report in one
    # member is untrusted -> filtered out before the intersection, so
    # coverage never completes even though the raw digest lines up.
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry("sha256:P1", 0.9, owner="dev", tier="verified")],
        "wiz-orc-cha-mal": [_entry("sha256:P1", 0.9, owner="stranger", tier="self-reported")],
    })
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    tree, chosen = select_parent(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, seed, owner="dev")
    assert tree == seed and chosen is None
    assert hub.calls == ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]


def test_top_trusted_elite_set_picks_max_mean_score(tmp_path):
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry("sha256:P1", 0.6), _entry("sha256:P3", 0.9)],
        "wiz-orc-cha-mal": [_entry("sha256:P1", 0.4), _entry("sha256:P3", 0.7)],
    })
    store = LocalTreeStore(tmp_path / "store")
    seen = {}
    def spy(entry, dest):
        seen["digest"] = entry["solution_digest"]
        return None
    result = top_trusted_elite(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, "dev", fetch=spy)
    assert result is None                  # unresolvable bytes here -> None
    assert seen["digest"] == "sha256:P3"    # mean .8 beats P1's mean .5 -> greedy max


def test_top_trusted_elite_set_empty_intersection_returns_none(tmp_path):
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_entry("sha256:P2", 0.9)],
        "wiz-orc-cha-mal": [],
    })
    store = LocalTreeStore(tmp_path / "store")
    assert top_trusted_elite(hub, "wiz-elf-cha-mal,wiz-orc-cha-mal", store, "dev") is None
    assert hub.calls == ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]


# -- influence_pool: union (not intersection) over identities (Task C1) -----

def test_influence_pool_unions_specialists(tmp_path):
    hub = _HubByIdentity({"wiz-elf-cha-mal": [_entry("sha256:A", 0.6)],
                          "wiz-orc-cha-mal": [_entry("sha256:B", 0.4)]})
    pool = influence_pool(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), "dev")
    digests = {e["solution_digest"] for e in pool}
    assert digests == {"sha256:A", "sha256:B"}   # UNION (coverage-gated would be empty)


def test_influence_pool_keeps_one_entry_per_column_for_a_generalist(tmp_path):
    # A program that is a trusted elite in BOTH queried identities appears
    # TWICE -- once per identity-column, each carrying that column's own
    # score -- NOT deduped to one entry (unlike _coverage_gated_entries's
    # intersection collapse). This is the emergent coverage-weighting the
    # brief calls for: a full-S generalist shows up in many columns, which
    # a future "dedupe cleanup" (natural-looking since the sibling function
    # dedupes) must not silently break.
    hub = _HubByIdentity({"wiz-elf-cha-mal": [_entry("sha256:G", 0.6)],
                          "wiz-orc-cha-mal": [_entry("sha256:G", 0.8)]})
    pool = influence_pool(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), "dev")
    entries = [e for e in pool if e["solution_digest"] == "sha256:G"]
    assert len(entries) == 2                              # once per column, not deduped
    by_identity = {e["identity"]: e["score"] for e in entries}
    assert by_identity == {"wiz-elf-cha-mal": 0.6, "wiz-orc-cha-mal": 0.8}


# -- sample_seeds: draw + resolve up to n distinct pool solutions (Task C2) -
# Pool entries here are ATOM identities (solution_digest = "<repo>@<commit>",
# matching repo/commit_sha) so _resolve's trust-the-pulled-commit path
# applies, same as test_resolve_atom_identity_trusts_pulled_commit.

def _atom_entry(repo_suffix: str, commit_char: str, score: float,
                 owner: str = "dev", tier: str = "verified") -> dict:
    repo = f"github.com/o/r{repo_suffix}"
    commit = commit_char * 40
    return {"solution_digest": f"{repo}@{commit}", "score": score, "owner": owner,
            "tier": tier, "repo": repo, "commit_sha": commit}


def _atom_fetch(entry, dest):
    (Path(dest) / "bot.py").write_text("pulled-code")
    return Path(dest)


def test_sample_seeds_resolves_distinct_entries_when_pool_has_enough(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_atom_entry("1", "a", 0.9), _atom_entry("2", "b", 0.6)],
        "wiz-orc-cha-mal": [_atom_entry("3", "c", 0.7)],
    })
    result = sample_seeds(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=3, fetch=_atom_fetch, rng=random.Random(0))
    assert len(result) == 3
    digests = [entry["solution_digest"] for _, entry in result if entry is not None]
    assert len(digests) == 3 and len(set(digests)) == 3   # all resolved, all distinct
    for path, entry in result:
        assert entry is not None
        assert "identity" in entry and "score" in entry
        assert store.has(entry["solution_digest"])
        assert path == store.path(entry["solution_digest"])


def test_sample_seeds_pads_with_seed_when_pool_is_thin(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    hub = _HubByIdentity({                                # only 2 distinct solutions total
        "wiz-elf-cha-mal": [_atom_entry("1", "a", 0.9)],
        "wiz-orc-cha-mal": [_atom_entry("2", "b", 0.6)],
    })
    result = sample_seeds(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=3, fetch=_atom_fetch, rng=random.Random(0))
    assert len(result) == 3
    resolved = [(p, e) for p, e in result if e is not None]
    padded = [(p, e) for p, e in result if e is None]
    assert len(resolved) == 2 and len(padded) == 1        # pool had only 2 -> 1 pad
    for path, entry in resolved:
        assert store.has(entry["solution_digest"])
        assert path == store.path(entry["solution_digest"])
        assert "identity" in entry and "score" in entry
    assert padded == [(seed, None)]


def test_sample_seeds_hub_error_returns_all_seed_pads(tmp_path):
    class _Boom:
        def elites(self, o): raise RuntimeError("down")
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    result = sample_seeds(_Boom(), ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=3)
    assert result == [(seed, None), (seed, None), (seed, None)]


# -- sample_seeds fix round 1: coverage gaps flagged by review --------------

def test_sample_seeds_continues_past_resolve_failure_to_other_pool_entries(tmp_path):
    # Regression guard: a resolve failure must DROP that digest and keep
    # drawing from what remains, not treat the failure as terminal (e.g. an
    # `else: break` after a failed _resolve) -- which would pass every other
    # sample_seeds test (they all use an always-succeeding fetch) while
    # silently degrading seed diversity down to all-padding.
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    fail_entry = _atom_entry("1", "a", 0.9)      # higher score -> drawn FIRST (k=1 greedy)
    ok_entry = _atom_entry("2", "b", 0.5)        # lower score -> drawn only if drawing continues
    fail_digest = fail_entry["solution_digest"]
    ok_digest = ok_entry["solution_digest"]
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [fail_entry],
        "wiz-orc-cha-mal": [ok_entry],
    })
    def fetch(entry, dest):
        if entry["solution_digest"] == fail_digest:
            return None                          # simulate a fetch/integrity failure
        (Path(dest) / "bot.py").write_text("pulled-code")
        return Path(dest)
    result = sample_seeds(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=2, fetch=fetch, rng=random.Random(0))
    assert len(result) == 2                                    # still padded to n
    resolved = [(p, e) for p, e in result if e is not None]
    assert len(resolved) == 1
    assert resolved[0][1]["solution_digest"] == ok_digest       # the OTHER entry DID resolve
    assert store.has(ok_digest) and resolved[0][0] == store.path(ok_digest)
    assert not store.has(fail_digest)                           # never cached -- fetch failed
    digests_present = {e["solution_digest"] for _, e in result if e is not None}
    assert fail_digest not in digests_present                   # failed digest ABSENT
    assert [(p, e) for p, e in result if e is None] == [(seed, None)]


def test_sample_seeds_stops_early_when_pool_exceeds_n(tmp_path):
    # 4 distinct solutions, n=2: proves `while len(out) < n` stops drawing
    # once n is reached, rather than resolving (and caching) the whole pool
    # before truncating -- checked via the fetch call count, not just the
    # output length (which is always n regardless of how much work happened).
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_atom_entry("1", "a", 0.9), _atom_entry("2", "b", 0.8)],
        "wiz-orc-cha-mal": [_atom_entry("3", "c", 0.7), _atom_entry("4", "d", 0.6)],
    })
    fetched = []
    def fetch(entry, dest):
        fetched.append(entry["solution_digest"])
        (Path(dest) / "bot.py").write_text("pulled-code")
        return Path(dest)
    result = sample_seeds(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=2, fetch=fetch, rng=random.Random(0))
    assert len(result) == 2
    digests = {e["solution_digest"] for _, e in result if e is not None}
    assert len(digests) == 2                      # exactly n resolved, all distinct
    assert all(e is not None for _, e in result)   # pool covers n -> no padding needed
    assert len(fetched) == 2                       # stopped after n, didn't drain all 4


def test_sample_seeds_generalist_in_two_columns_occupies_one_slot(tmp_path):
    # Same program elite in both queried identities appears TWICE in
    # influence_pool (once per column, C1's emergent coverage-weighting) --
    # sample_seeds must still count it as ONE distinct solution, not let it
    # consume two of the n slots.
    store = LocalTreeStore(tmp_path / "store")
    seed = _tree(tmp_path, "seed")
    generalist_digest = _atom_entry("g", "e", 0.6)["solution_digest"]
    hub = _HubByIdentity({
        "wiz-elf-cha-mal": [_atom_entry("g", "e", 0.6)],
        "wiz-orc-cha-mal": [_atom_entry("g", "e", 0.8)],   # same repo@commit -> same digest
    })
    result = sample_seeds(hub, ("wiz-elf-cha-mal", "wiz-orc-cha-mal"), store, seed,
                          owner="dev", n=2, fetch=_atom_fetch, rng=random.Random(0))
    assert len(result) == 2
    resolved = [(p, e) for p, e in result if e is not None]
    assert len(resolved) == 1                                    # ONE slot, not two
    assert resolved[0][1]["solution_digest"] == generalist_digest
    assert store.has(generalist_digest)
    assert [(p, e) for p, e in result if e is None] == [(seed, None)]


# -- per_identity_elites: thin per-identity read for MAP-Elites cell seeding (Task B3) --

def test_per_identity_elites_returns_top_trusted_with_resolved_tree(tmp_path):
    from nethackers.harness import select
    from nethackers.harness.store import LocalTreeStore
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a = {"solution_digest": "github.com/x/repo@" + "a" * 40, "score": 0.9,
               "owner": "dev", "tier": "verified", "repo": "github.com/x/repo",
               "commit_sha": "a" * 40}
    lo_a = {**entry_a, "solution_digest": "github.com/x/repo@" + "c" * 40,
            "score": 0.1, "commit_sha": "c" * 40}
    class _Hub:
        def elites(self, objective):
            return {id_a: [lo_a, entry_a], id_b: []}.get(objective, [])
    def fetch(entry, dest):
        dest = Path(dest)
        (dest / "bot.py").write_text("x")
        (dest / "nethackers.solution.json").write_text('{"root":".","entrypoint":"bot.py"}')
        return dest
    out = select.per_identity_elites(
        _Hub(), (id_a, id_b), "dev",
        store=LocalTreeStore(tmp_path / "store"), fetch=fetch)
    assert id_a in out and id_b not in out          # id_b has no elite
    entry, tree = out[id_a]
    assert entry["solution_digest"].endswith("a" * 40)   # top score, not lo_a
    assert (tree / "bot.py").exists()
