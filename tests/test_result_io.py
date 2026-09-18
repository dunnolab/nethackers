import json, os, pytest
from pathlib import Path
from nethackers.arena.result_io import read_result_json, ResultError

def test_reads_valid_list(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps([{"progress": 0.1}]))
    assert read_result_json(tmp_path) == [{"progress": 0.1}]

def test_refuses_symlink(tmp_path):
    (tmp_path / "secret").write_text("[]")
    os.symlink(tmp_path / "secret", tmp_path / "results.json")
    with pytest.raises(ResultError):
        read_result_json(tmp_path)

def test_refuses_oversize(tmp_path):
    (tmp_path / "results.json").write_text("[" + "0," * 5_000_000 + "0]")
    with pytest.raises(ResultError):
        read_result_json(tmp_path, max_bytes=1000)

def test_refuses_non_list(tmp_path):
    (tmp_path / "results.json").write_text('{"not": "a list"}')
    with pytest.raises(ResultError):
        read_result_json(tmp_path)
