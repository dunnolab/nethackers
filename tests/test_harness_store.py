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


def test_save_is_idempotent_when_dest_exists(tmp_path):
    src = _tree(tmp_path / "src", "x = 1\n")
    store = LocalTreeStore(tmp_path / "store")
    d1 = store.save(src)
    d2 = store.save(src)                                   # dest already there
    assert d1 == d2
    assert (store.path(d1) / "bot.py").read_text() == "x = 1\n"
    assert not list((tmp_path / "store").glob(".tmp-*"))  # no temp leaked

def test_save_leaves_a_preexisting_dest_untouched(tmp_path):
    src = _tree(tmp_path / "src", "new\n")
    store = LocalTreeStore(tmp_path / "store")
    digest = _solution_digest(src)
    dest = store.path(digest)
    dest.mkdir(parents=True)
    (dest / "bot.py").write_text("winner\n")              # concurrent winner already published
    assert store.save(src) == digest
    assert (dest / "bot.py").read_text() == "winner\n"    # not overwritten
    assert not list((tmp_path / "store").glob(".tmp-*"))

def test_save_swallows_rename_race_and_cleans_temp(tmp_path, monkeypatch):
    src = _tree(tmp_path / "src", "x\n")
    store = LocalTreeStore(tmp_path / "store")
    digest = _solution_digest(src)

    def racing_rename(a, b):
        Path(b).mkdir(parents=True, exist_ok=True)        # winner appears between check and rename
        raise OSError("Directory not empty")
    monkeypatch.setattr("nethackers.harness.store.os.rename", racing_rename)

    store.save(src)                                        # must not raise
    assert store.path(digest).is_dir()                    # dest present (winner's)
    assert not list((tmp_path / "store").glob(".tmp-*"))  # our temp cleaned

def test_save_as_idempotent_when_dest_exists(tmp_path):
    src = _tree(tmp_path / "src", "code\n")
    store = LocalTreeStore(tmp_path / "store")
    atom = "github.com/o/r@abc123"
    store.save_as(atom, src)
    store.save_as(atom, src)                              # second: dest exists, no raise
    assert (store.path(atom) / "bot.py").read_text() == "code\n"
    assert not list((tmp_path / "store").glob(".tmp-*"))
