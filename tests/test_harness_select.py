# tests/test_harness_select.py
from pathlib import Path

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


# -- B5: dead island SELECT machinery removed --------------------------------

def test_dead_select_machinery_removed():
    from nethackers.harness import select
    for name in ("select_parent", "_coverage_gated_entries", "influence_pool",
                 "sample_seeds", "top_trusted_elite"):
        assert not hasattr(select, name)
