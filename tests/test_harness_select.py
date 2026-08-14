# tests/test_harness_select.py
import random
from pathlib import Path

from nethackers.harness.select import select_parent
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
