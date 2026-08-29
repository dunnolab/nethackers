# tests/test_harness_select.py
from pathlib import Path

# -- per_identity_elites: thin per-identity read for MAP-Elites cell seeding (Task B3) --
# Row shape (Task 2b): {rank, identity, program_id, owner, score,
# reference: {repo, commit}} -- program_id is the cache key/id (was
# solution_digest); reference is what pull_fetch git-pulls; tier is gone
# (/elites' tier is always the constant "self-reported", so _trusted no
# longer checks it -- owner match only).


def test_per_identity_elites_returns_top_trusted_with_resolved_tree(tmp_path):
    from nethackers.harness import select
    from nethackers.harness.store import LocalTreeStore
    id_a, id_b = "wiz-elf-cha-mal", "wiz-orc-cha-mal"
    entry_a = {"program_id": "prog_" + "a" * 32, "score": 0.9, "owner": "dev",
               "reference": {"repo": "github.com/x/repo", "commit": "a" * 40}}
    lo_a = {**entry_a, "program_id": "prog_" + "c" * 32, "score": 0.1,
            "reference": {"repo": "github.com/x/repo", "commit": "c" * 40}}
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
    assert entry["program_id"] == entry_a["program_id"]   # top score, not lo_a
    assert (tree / "bot.py").exists()


def test_trusted_is_owner_match_only_not_a_verified_tier(tmp_path):
    # _trusted no longer checks a "verified" tier (Task 2b: /elites' tier is
    # always the constant "self-reported", so that branch never fired) --
    # only entry["owner"] == the run's owner. A stranger's elite (even a
    # high scorer) is excluded; your own is kept.
    from nethackers.harness import select
    from nethackers.harness.store import LocalTreeStore
    ident = "wiz-elf-cha-mal"
    stranger = {"program_id": "prog_s", "score": 0.99, "owner": "someone-else",
                "reference": {"repo": "github.com/them/repo", "commit": "e" * 40}}
    mine = {"program_id": "prog_m", "score": 0.1, "owner": "dev",
            "reference": {"repo": "github.com/dev/repo", "commit": "f" * 40}}
    class _Hub:
        def elites(self, objective):
            return {ident: [stranger, mine]}.get(objective, [])
    def fetch(entry, dest):
        dest = Path(dest)
        (dest / "bot.py").write_text("x")
        (dest / "nethackers.solution.json").write_text('{"root":".","entrypoint":"bot.py"}')
        return dest
    out = select.per_identity_elites(
        _Hub(), (ident,), "dev",
        store=LocalTreeStore(tmp_path / "store"), fetch=fetch)
    entry, _tree = out[ident]
    assert entry["program_id"] == "prog_m"   # the stranger's higher score is untrusted


def test_pull_fetch_pulls_the_reference_repo_at_commit(tmp_path, monkeypatch):
    # The real (default) cache-miss resolver -- reads reference:{repo,commit}
    # off the /elites row and git-pulls "repo@commit". (The other tests above
    # inject a custom `fetch=` and never exercise this default.)
    from nethackers.harness import select
    calls = []
    def fake_pull(repo_at_commit, dest):
        calls.append(repo_at_commit)
        Path(dest).mkdir(parents=True, exist_ok=True)
        return Path(dest)
    monkeypatch.setattr(select, "pull", fake_pull)
    entry = {"program_id": "prog_x", "score": 0.5, "owner": "dev",
             "reference": {"repo": "github.com/x/repo", "commit": "b" * 40}}
    dest = tmp_path / "dest"

    out = select.pull_fetch(entry, dest)

    assert calls == [f"github.com/x/repo@{'b' * 40}"]
    assert out == dest


# -- B5: dead island SELECT machinery removed --------------------------------

def test_dead_select_machinery_removed():
    from nethackers.harness import select
    for name in ("select_parent", "_coverage_gated_entries", "influence_pool",
                 "sample_seeds", "top_trusted_elite"):
        assert not hasattr(select, name)
