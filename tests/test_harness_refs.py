from pathlib import Path

from nethackers.harness.refs import assemble


def _tree(p: Path, code: str) -> Path:
    p.mkdir(parents=True)
    (p / "bot.py").write_text(code)
    (p / "__pycache__").mkdir()
    (p / "__pycache__" / "x.pyc").write_text("x")
    return p


def test_assemble_lays_out_refs_and_manifest_without_junk(tmp_path):
    inf = _tree(tmp_path / "inf", "influence")
    att = _tree(tmp_path / "att", "attempt")
    dest = tmp_path / "refs"
    assemble(dest, base_eval='[{"progress":0.1}]',
             influences=[("hub-elite-9d09", inf, "score 0.17; strong at mon-hum-cha-mal")],
             attempts=[("iter-14", att, "score 0.09; hypothesis: raised HP threshold")])
    assert (dest / "influences" / "hub-elite-9d09" / "bot.py").read_text() == "influence"
    assert not (dest / "influences" / "hub-elite-9d09" / "__pycache__").exists()  # junk excluded
    assert (dest / "attempts" / "iter-14" / "bot.py").read_text() == "attempt"
    assert (dest / "parent-eval.json").read_text().startswith("[")
    ctx = (dest / "CONTEXT.md").read_text()
    assert "hub-elite-9d09" in ctx and "iter-14" in ctx and "mon-hum-cha-mal" in ctx


def test_assemble_copies_the_pristine_parent(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "bot.py").write_text("VERSION = 0\n")
    (parent / "__pycache__").mkdir()
    (parent / "__pycache__" / "x.pyc").write_text("junk")
    dest = tmp_path / "refs"
    assemble(dest, base_eval=None, influences=[], attempts=[], parent=parent)
    assert (dest / "parent" / "bot.py").read_text() == "VERSION = 0\n"
    assert not (dest / "parent" / "__pycache__").exists()   # junk excluded
    ctx = (dest / "CONTEXT.md").read_text()
    assert "parent/" in ctx and "/refs/parent" in ctx       # manifest points at it
