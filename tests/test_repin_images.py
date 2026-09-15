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


ARENA = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64
MUTATOR = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "b" * 64
BASE = "ghcr.io/dunnolab/nethackers-nle-base@sha256:" + "c" * 64
INPUTS = "sha256:" + "d" * 64


def test_render_pins_round_trips_all_four_values():
    ns = _exec(repin.render_pins(ARENA, MUTATOR, BASE, INPUTS))
    assert (ns["ARENA_IMAGE"], ns["MUTATOR_IMAGE"], ns["NLE_BASE_IMAGE"],
            ns["MUTATOR_INPUTS"]) == (ARENA, MUTATOR, BASE, INPUTS)


@pytest.mark.parametrize("bad", [
    "ghcr.io/x/arena:latest",                       # a mutable tag, not a digest
    "ghcr.io/x/arena@sha256:tooshort",              # not 64 hex
    "ghcr.io/x/arena@sha256:" + "A" * 64,           # uppercase hex
    "arena@sha256:" + "a" * 63,                      # 63 hex
])
def test_render_pins_rejects_a_non_digest_ref(bad):
    with pytest.raises(ValueError):
        repin.render_pins(bad, MUTATOR, BASE, INPUTS)


@pytest.mark.parametrize("bad", ["d" * 64, "sha256:" + "D" * 64, "sha256:" + "d" * 63])
def test_render_pins_rejects_a_malformed_fingerprint(bad):
    with pytest.raises(ValueError):
        repin.render_pins(ARENA, MUTATOR, BASE, bad)


def test_main_writes_all_four_pins(tmp_path):
    out = tmp_path / "_image_pins.py"
    rc = repin.main(["--arena", ARENA, "--mutator", MUTATOR, "--nle-base", BASE,
                     "--mutator-inputs", INPUTS, "--out", str(out)])
    assert rc == 0
    assert _exec(out.read_text())["NLE_BASE_IMAGE"] == BASE


def test_main_updates_the_mutator_alone_and_keeps_the_rest_byte_identical(tmp_path):
    # The automatic mutator rebuild passes only --mutator/--mutator-inputs; the
    # arena and base pins it didn't build must not change.
    out = tmp_path / "_image_pins.py"
    out.write_text(repin.render_pins(ARENA, MUTATOR, BASE, INPUTS))
    new_mutator = MUTATOR.replace("b" * 64, "e" * 64)
    new_inputs = "sha256:" + "f" * 64
    assert repin.main(["--mutator", new_mutator, "--mutator-inputs", new_inputs,
                       "--out", str(out)]) == 0
    assert out.read_text() == repin.render_pins(ARENA, new_mutator, BASE, new_inputs)


def test_main_refuses_when_a_value_is_neither_given_nor_on_file(tmp_path):
    out = tmp_path / "_image_pins.py"
    rc = repin.main(["--arena", ARENA, "--mutator", MUTATOR, "--out", str(out)])
    assert rc == 2 and not out.exists()


def test_main_rejects_a_bad_ref_without_writing(tmp_path):
    out = tmp_path / "_image_pins.py"
    rc = repin.main(["--arena", "not-a-ref", "--mutator", MUTATOR, "--nle-base", BASE,
                     "--mutator-inputs", INPUTS, "--out", str(out)])
    assert rc == 2 and not out.exists()


def test_committed_pins_file_matches_the_generator():
    # Drift guard: the checked-in _image_pins.py is byte-for-byte what
    # render_pins produces for its own values, so it stays generated (spec D11).
    from nethackers import _image_pins
    regenerated = repin.render_pins(_image_pins.ARENA_IMAGE, _image_pins.MUTATOR_IMAGE,
                                    _image_pins.NLE_BASE_IMAGE, _image_pins.MUTATOR_INPUTS)
    assert regenerated == _PINS.read_text()
