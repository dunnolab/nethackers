# tests/test_stage_discovery.py
from pathlib import Path

from nethackers.config import Stage, load_stage


def _write_stack(d: Path, port=28417):
    (d / ".env.stack").write_text(
        f"NETHACKERS_STAGE=wt\nNETHACKERS_HUB=http://localhost:{port}\n"
        f"NETHACKERS_HUB_PORT={port}\nNETHACKERS_DATA_ROOT={d}/.nethackers\n")


def test_walk_up_discovers_stack(tmp_path):
    _write_stack(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    s = load_stage(cwd=nested, environ={})
    assert s.name == "wt" and s.hub_port == 28417
    assert s.data_root == tmp_path / ".nethackers"


def test_no_file_is_prod(tmp_path):
    assert load_stage(cwd=tmp_path, environ={}) == Stage()


def test_stage_file_env_targets_explicit(tmp_path):
    _write_stack(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    s = load_stage(cwd=other, environ={"NETHACKERS_STAGE_FILE": str(tmp_path / ".env.stack")})
    assert s.name == "wt"


def test_empty_stage_file_env_ignores_discovery(tmp_path):
    _write_stack(tmp_path)
    s = load_stage(cwd=tmp_path, environ={"NETHACKERS_STAGE_FILE": ""})
    assert s == Stage()                                   # forced prod


def test_process_env_beats_file(tmp_path):
    _write_stack(tmp_path, port=28417)
    s = load_stage(cwd=tmp_path, environ={"NETHACKERS_HUB_PORT": "9999"})
    assert s.hub_port == 9999                             # env > file
