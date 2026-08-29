import re

from nethackers import _image_pins


def test_pins_are_ghcr_digest_refs():
    pattern = r"ghcr\.io/dunnolab/nethackers-(arena|mutator)@sha256:[0-9a-f]{64}"
    for ref in (_image_pins.ARENA_IMAGE, _image_pins.MUTATOR_IMAGE):
        assert re.fullmatch(pattern, ref)
