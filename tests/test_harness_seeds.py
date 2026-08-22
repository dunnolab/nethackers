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
    # dev_spec now resolves via selector.resolve(), which raises ValueError
    # for unrecognized tokens (not a raw dict-lookup KeyError anymore).
    with pytest.raises(ValueError):
        dev_spec("nope")

def test_validation_inherits_dev_max_steps_by_default():
    dev = dev_spec("val-dwa-law-fem")
    val = validation_spec("val-dwa-law-fem", n=5, start=1000)
    assert val.max_steps == dev.max_steps

def test_validation_max_steps_override_caps_the_spec():
    val = validation_spec("val-dwa-law-fem", n=1, start=9000, max_steps=2000)
    assert val.max_steps == 2000

# --- generalist (role/list/glob) selector tokens -------------------------

def test_dev_spec_single_identity_unchanged():
    assert dev_spec("wiz-elf-cha-mal") is CATALOG["wiz-elf-cha-mal"]

def test_dev_spec_random_unchanged():
    assert dev_spec("random") is CATALOG["random"]

def test_dev_spec_all_rejected_with_hint():
    with pytest.raises(ValueError, match=r"\*"):
        dev_spec("all")

def test_dev_spec_role_is_a_union():
    spec = dev_spec("wiz")
    idents = {c for _s, c in spec.batch}
    assert all(i.startswith("wiz-") for i in idents)
    assert spec.kind == "set"

def test_validation_spec_single_identity_unchanged():
    v = validation_spec("wiz-elf-cha-mal", n=15)
    assert {c for _s, c in v.batch} == {"wiz-elf-cha-mal"}
    assert {s for s, _c in v.batch} == set(range(1000, 1015))

def test_validation_spec_set_spans_all_members_on_heldout_seeds():
    v = validation_spec("wiz", n=3, start=1000)
    dev_idents = {c for _s, c in dev_spec("wiz").batch}
    assert {c for _s, c in v.batch} == dev_idents
    assert {s for s, _c in v.batch} == {1000, 1001, 1002}
    assert len(v.batch) == 3 * len(dev_idents)
