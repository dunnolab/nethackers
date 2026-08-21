import pytest
from nethackers.hub.selector import ResolvedObjective, resolve
from nethackers.hub.objectives import IDENTITIES, ROLES, VALID, ROLE_GENDERS


def _role_members(role: str) -> tuple[str, ...]:
    return tuple(sorted(i for i in IDENTITIES if i.startswith(f"{role}-")))


def test_single_identity_passthrough():
    r = resolve("wiz-elf-cha-mal")
    assert r == ResolvedObjective("wiz-elf-cha-mal", ("wiz-elf-cha-mal",), "single")


def test_random_passthrough():
    assert resolve("random") == ResolvedObjective("random", (), "random")


def test_all_is_all_kind_with_every_identity():
    r = resolve("all")
    assert r.kind == "all"
    assert r.identities == tuple(IDENTITIES)


def test_bare_role_expands_to_its_identities():
    r = resolve("wiz")
    assert r.kind == "set"
    assert r.name == "wiz"
    assert r.identities == _role_members("wiz")
    assert all(i.startswith("wiz-") for i in r.identities)


def test_comma_list_of_identities():
    r = resolve("wiz-elf-cha-mal,val-dwa-law-fem")
    assert r.kind == "set"
    assert r.identities == ("val-dwa-law-fem", "wiz-elf-cha-mal")  # sorted+deduped


def test_glob_over_race():
    r = resolve("*-elf-*-*")
    assert r.kind == "set"
    assert r.identities == tuple(sorted(i for i in IDENTITIES if i.split("-")[1] == "elf"))


def test_glob_role_prefix_equals_bare_role():
    assert resolve("wiz-*").identities == resolve("wiz").identities


def test_unknown_raises():
    with pytest.raises(ValueError):
        resolve("nonsense-xyz")


def test_empty_raises():
    with pytest.raises(ValueError):
        resolve("   ")


def test_list_with_unknown_member_raises():
    with pytest.raises(ValueError):
        resolve("wiz-elf-cha-mal,not-a-build")


def test_single_member_glob_is_still_a_set_kind():
    # a glob that happens to match exactly one identity is still kind "set"
    only = IDENTITIES[0]
    r = resolve(only.replace(only.split("-")[3], "*"))  # e.g. wiz-elf-cha-*
    assert r.kind == "set"
