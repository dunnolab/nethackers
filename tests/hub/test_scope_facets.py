import pytest
from nethackers.hub.objectives import IDENTITIES, GENDERS
from nethackers.hub.views.boards import resolve_scope


def _facet(ids, index, value):
    return all(i.split("-")[index] == value for i in ids) and len(ids) > 0


def test_gender_vocab():
    assert GENDERS == ("mal", "fem")


def test_facet_scopes_resolve_by_position():
    assert resolve_scope("role:val") == ("role", tuple(i for i in IDENTITIES if i.split("-")[0] == "val"))
    assert _facet(resolve_scope("race:elf")[1], 1, "elf")
    assert _facet(resolve_scope("align:law")[1], 2, "law")
    assert _facet(resolve_scope("gender:fem")[1], 3, "fem")


def test_legacy_tokens_still_work():           # additive: nothing old breaks
    assert resolve_scope("generalist")[0] == "generalist"
    assert resolve_scope("val")[0] == "role"   # bare role, unchanged


def test_unknown_facet_value_raises():
    with pytest.raises(ValueError):
        resolve_scope("race:notarace")
    with pytest.raises(ValueError):
        resolve_scope("bogus:val")
