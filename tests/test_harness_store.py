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
