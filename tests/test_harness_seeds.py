# tests/test_harness_seeds.py
import pytest

from nethackers.harness.seeds import dev_spec, validation_spec
from nethackers.hub.objectives import CATALOG


def test_dev_spec_is_the_published_batch():
    spec = dev_spec("val-dwa-law-fem")
    assert spec is CATALOG["val-dwa-law-fem"]

def test_validation_is_disjoint_fresh_seeds_same_character():
    dev = dev_spec("val-dwa-law-fem")
    val = validation_spec("val-dwa-law-fem", n=5, start=1000)
    dev_seeds = {s for s, _ in dev.batch}
    val_seeds = {s for s, _ in val.batch}
    assert val_seeds.isdisjoint(dev_seeds)
    assert val_seeds == set(range(1000, 1005))
    assert {c for _, c in val.batch} == {"val-dwa-law-fem"}   # same character
    assert val.name == "val-dwa-law-fem__validation"

def test_unknown_objective_raises():
    with pytest.raises(KeyError):
        dev_spec("nope")

def test_validation_inherits_dev_max_steps_by_default():
    dev = dev_spec("val-dwa-law-fem")
    val = validation_spec("val-dwa-law-fem", n=5, start=1000)
    assert val.max_steps == dev.max_steps

def test_validation_max_steps_override_caps_the_spec():
    val = validation_spec("val-dwa-law-fem", n=1, start=9000, max_steps=2000)
    assert val.max_steps == 2000
