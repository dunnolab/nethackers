"""``nethackers --version``: offline (no Docker/network -- reads pins only,
spec 5.7/INV7), honors ``-o json``, prints all three version regimes
(package, run-schema, both pinned image refs)."""
import json

import nethackers.cli as cli
from nethackers import _image_pins
from nethackers.harness.version import RUN_SCHEMA_VERSION


def test_version_json(capsys):
    rc = cli.main(["--version", "-o", "json"])
    out = json.loads(capsys.readouterr().out)
    assert out["run_schema_version"] == RUN_SCHEMA_VERSION
    assert out["images"]["arena"] == _image_pins.ARENA_IMAGE
    assert out["images"]["mutator"] == _image_pins.MUTATOR_IMAGE
    assert rc == 0


def test_version_human_lists_all_three_regimes(capsys):
    cli.main(["--version"])
    out = capsys.readouterr().out
    assert "run-schema" in out and "arena" in out and "mutator" in out
