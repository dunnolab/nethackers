"""``scripts/repin_images.py`` — the generator that ``sandbox-images.yml`` runs
to rewrite ``src/nethackers/_image_pins.py`` from freshly-pushed GHCR digests
(so the re-pin ceremony is a generated PR, not a hand-paste)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PINS = _ROOT / "src" / "nethackers" / "_image_pins.py"


def _load_script():
    # scripts/ isn't a package -- load the module straight from its path.
    path = _ROOT / "scripts" / "repin_images.py"
    spec = importlib.util.spec_from_file_location("repin_images", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


repin = _load_script()


def _exec(source: str) -> dict:
    ns: dict = {}
    exec(compile(source, "<pins>", "exec"), ns)  # noqa: S102 -- deliberate, generated source
    return ns


def test_render_pins_round_trips_the_two_digest_refs():
    arena = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64
    mutator = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "b" * 64
    ns = _exec(repin.render_pins(arena, mutator))
    assert ns["ARENA_IMAGE"] == arena
    assert ns["MUTATOR_IMAGE"] == mutator


@pytest.mark.parametrize("bad", [
    "ghcr.io/x/arena:latest",                       # a mutable tag, not a digest
    "ghcr.io/x/arena@sha256:tooshort",              # not 64 hex
    "ghcr.io/x/arena@sha256:" + "A" * 64,           # uppercase hex
    "arena@sha256:" + "a" * 63,                      # 63 hex
])
def test_render_pins_rejects_a_non_digest_ref(bad):
    with pytest.raises(ValueError):
        repin.render_pins(bad, "ghcr.io/x/m@sha256:" + "b" * 64)


def test_main_writes_the_out_file(tmp_path):
    out = tmp_path / "_image_pins.py"
    rc = repin.main([
        "--arena", "ghcr.io/dunnolab/nethackers-arena@sha256:" + "c" * 64,
        "--mutator", "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "d" * 64,
        "--out", str(out)])
    assert rc == 0 and out.exists()
    assert _exec(out.read_text())["ARENA_IMAGE"].endswith("c" * 64)


def test_main_rejects_a_bad_ref_without_writing(tmp_path):
    out = tmp_path / "_image_pins.py"
    rc = repin.main(["--arena", "not-a-ref", "--mutator", "ghcr.io/x/m@sha256:" + "b" * 64,
                     "--out", str(out)])
    assert rc == 2 and not out.exists()


def test_committed_pins_file_matches_the_generator():
    # Drift guard: the checked-in _image_pins.py is byte-for-byte what
    # render_pins would produce for its own current digests -- so a hand-edit
    # (or a generator tweak) that drifts the two apart fails here, keeping the
    # file a purely generated artifact (spec D11).
    from nethackers import _image_pins
    regenerated = repin.render_pins(_image_pins.ARENA_IMAGE, _image_pins.MUTATOR_IMAGE)
    assert regenerated == _PINS.read_text()
