# tests/test_harness_store.py
from pathlib import Path

from nethackers.eval.runner import _solution_digest
from nethackers.harness.store import LocalTreeStore


def _tree(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bot.py").write_text(text)
    return root

def test_save_roundtrips_tree_and_keys_by_digest(tmp_path):
    src = _tree(tmp_path / "src", "x = 1\n")
    store = LocalTreeStore(tmp_path / "store")
    digest = store.save(src)
    assert digest == _solution_digest(src)          # keyed by content digest
    assert store.has(digest)
    stored = store.path(digest)
    assert (stored / "bot.py").read_text() == "x = 1\n"
    assert store.path(digest).name == digest.split(":", 1)[1]  # dir name = hex suffix

def test_has_is_false_for_unknown(tmp_path):
    store = LocalTreeStore(tmp_path / "store")
    assert not store.has("sha256:deadbeef")


def test_path_has_saveas_handle_atom_repo_commit_digest(tmp_path):
    # Atom hub identities are "<host>/<owner>/<repo>@<commit>" (no colon), unlike
    # the local "sha256:<hex>" content digests. Regression: path() did
    # digest.split(":", 1)[1] -> IndexError on colon-less atom identities.
    store = LocalTreeStore(tmp_path / "store")
    atom = "github.com/vkurenkov/nethacker@9d09dd3b145978245d571f9f7ce55b3850a1e3a7"
    assert not store.has(atom)                        # must not raise
    src = _tree(tmp_path / "src", "code\n")
    store.save_as(atom, src)                           # cache under the atom key
    assert store.has(atom)
    assert (store.path(atom) / "bot.py").read_text() == "code\n"
    assert "/" not in store.path(atom).name           # flattened to one safe segment
