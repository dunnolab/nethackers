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
