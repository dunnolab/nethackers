from pathlib import Path

from nethackers.harness.refs import Attempt, assemble

IDS = ["val-hum-law-fem", "val-hum-neu-fem", "val-dwa-law-fem"]


def _mktree(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / "bot.py").write_text("x = 1\n")
    junk = p / "__pycache__"
    junk.mkdir()
    (junk / "bot.cpython-311.pyc").write_text("junk")
    return p


def test_assemble_writes_index_attempts_and_eval(tmp_path):
    parent = _mktree(tmp_path / "parent")
    a_tree = _mktree(tmp_path / "a24")
    attempts = [Attempt("24", a_tree, "Elbereth counterfire",
                        {"val-hum-law-fem": 0.141, "val-hum-neu-fem": 0.108,
                         "val-dwa-law-fem": 0.121}, 0.123,
                        '[{"trajectory_id":0,"progress":0.1}]')]
    dest = tmp_path / "refs"
    assemble(dest, parent=parent, parent_eval='[{"trajectory_id":0}]',
             attempts=attempts, identities=IDS)

    ctx = (dest / "CONTEXT.md").read_text()
    assert "parent-eval.json" in ctx and "attempts.md" in ctx and "parent/" in ctx
    att = (dest / "attempts.md").read_text()
    assert "Elbereth counterfire" in att
    assert "0.141" in att and "0.108" in att and "0.123" in att
    # declared column order, not alphabetical:
    assert (att.index("val-hum-law-fem") < att.index("val-hum-neu-fem")
            < att.index("val-dwa-law-fem"))
    for f in ("this run", "iteration", "cell", "champion", "dev ", "weakest", "held-out"):
        assert f not in att.lower() and f not in ctx.lower()

    assert (dest / "parent" / "bot.py").exists()
    assert not (dest / "parent" / "__pycache__").exists()          # junk filtered
    assert (dest / "attempts" / "24" / "bot.py").exists()
    assert not (dest / "attempts" / "24" / "__pycache__").exists()  # junk filtered
    assert (dest / "attempts" / "24" / "eval.json").read_text().startswith("[")
    assert (dest / "parent-eval.json").read_text().startswith("[")


def test_missing_identity_renders_dash_not_nan(tmp_path):
    parent = _mktree(tmp_path / "p")
    attempts = [Attempt("7", _mktree(tmp_path / "a7"), "partial",
                        {"val-hum-law-fem": 0.1}, 0.1, "")]   # missing 2 idents
    dest = tmp_path / "r"
    assemble(dest, parent=parent, parent_eval=None, attempts=attempts, identities=IDS)
    att = (dest / "attempts.md").read_text()
    assert "nan" not in att.lower()
    assert "—" in att                                          # em-dash for missing


def test_empty_attempts_renders_none_note(tmp_path):
    parent = _mktree(tmp_path / "p")
    dest = tmp_path / "r"
    assemble(dest, parent=parent, parent_eval=None, attempts=[], identities=IDS)
    assert (dest / "CONTEXT.md").exists()
    assert "(none yet)" in (dest / "attempts.md").read_text()


def test_hypothesis_none_does_not_crash(tmp_path):
    parent = _mktree(tmp_path / "p")
    attempts = [Attempt("9", _mktree(tmp_path / "a9"), None,
                        {i: 0.1 for i in IDS}, 0.1, "")]
    dest = tmp_path / "r"
    assemble(dest, parent=parent, parent_eval=None, attempts=attempts, identities=IDS)
    assert (dest / "attempts.md").read_text()                  # renders, no crash
