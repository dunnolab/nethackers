"""The mutator input fingerprint (``image_inputs.py``): what moves it, what must
not, and the Dockerfile contract that keeps its input list honest."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from nethackers import image_inputs

BASE = "ghcr.io/dunnolab/nethackers-nle-base@sha256:" + "a" * 64
REPO = Path(__file__).resolve().parents[1]
GOLDEN = "sha256:e44c29d002bf8bfd7350b0089f1eaa5563cd6c383d91493e4283bd3d3e6105f0"


def _tree(root: Path) -> Path:
    files = {
        "Dockerfile.mutator": "FROM base\n",
        "docker-entrypoint.sh": "#!/bin/sh\n",
        "src/nethackers/__init__.py": "",
        "src/nethackers/arena/run.py": "print(1)\n",
        "src/nethackers/contracts/models.py": "X = 1\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o644)
    (root / "docker-entrypoint.sh").chmod(0o755)
    return root


def test_scheme_v1_value_is_stable(tmp_path):
    # Every published h- tag depends on this exact scheme. Changing the scheme
    # means bumping image_inputs.SCHEME, never silently moving this value.
    assert image_inputs.mutator_inputs_hash(_tree(tmp_path), BASE) == GOLDEN


@pytest.mark.parametrize("change", ["content", "exec bit", "base"])
def test_any_input_change_moves_the_hash(tmp_path, change):
    root = _tree(tmp_path)
    base = BASE
    if change == "content":
        (root / "src/nethackers/arena/run.py").write_text("print(2)\n")
    elif change == "exec bit":
        (root / "Dockerfile.mutator").chmod(0o755)
    else:
        base = BASE.replace("a" * 64, "b" * 64)
    assert image_inputs.mutator_inputs_hash(root, base) != GOLDEN


def test_a_new_file_in_an_input_directory_moves_the_hash(tmp_path):
    root = _tree(tmp_path)
    (root / "src/nethackers/contracts/extra.py").write_text("Y = 2\n")
    assert image_inputs.mutator_inputs_hash(root, BASE) != GOLDEN


def test_files_the_build_never_copies_do_not_move_the_hash(tmp_path):
    root = _tree(tmp_path)
    (root / "src/nethackers/arena/__pycache__").mkdir()
    (root / "src/nethackers/arena/__pycache__/run.cpython-311.pyc").write_bytes(b"\0")
    (root / "src/nethackers/contracts/stray.pyc").write_bytes(b"\0")
    (root / "src/nethackers/contracts/.DS_Store").write_bytes(b"\0")
    (root / "README.md").write_text("not an input\n")
    assert image_inputs.mutator_inputs_hash(root, BASE) == GOLDEN


def test_every_dockerfile_copy_source_is_an_input():
    # A COPY outside the input list would change the image without changing its
    # fingerprint, so checkouts and CI would keep reusing a stale image.
    sources = [m.group(1) for line in (REPO / "Dockerfile.mutator").read_text().splitlines()
               if (m := re.match(r"\s*COPY\s+(?!--from)(\S+)\s+\S+", line))]
    assert sources
    for src in sources:
        assert any(src == p or src.startswith(p + "/")
                   for p in image_inputs.MUTATOR_INPUT_PATHS), src


def test_image_tag_drops_the_algorithm_prefix():
    assert image_inputs.image_tag("sha256:" + "c" * 64) == "h-" + "c" * 64


def test_main_prints_the_fingerprint(tmp_path, capsys):
    root = _tree(tmp_path)
    assert image_inputs.main(["mutator", "--base", BASE, "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == GOLDEN


def test_main_defaults_to_the_pinned_base(tmp_path, capsys, monkeypatch):
    from nethackers import _image_pins
    monkeypatch.setattr(_image_pins, "NLE_BASE_IMAGE", BASE)
    assert image_inputs.main(["mutator", "--root", str(_tree(tmp_path))]) == 0
    assert capsys.readouterr().out.strip() == GOLDEN
