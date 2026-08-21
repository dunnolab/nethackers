"""Tests for ``nethackers.hub.objectives``: the ~73 legal NetHack identities
and the published, deterministic ``(seed, character)`` objective catalog
(M2a Task 4).

The identity count is verified against NetHack 3.6.6's own ``role.c``
source -- see the module docstring in ``nethackers/hub/objectives.py`` for
the full citation and ``task-4-report.md`` for the adjudication summary.
Per ``task-4-context.md``'s explicit resolution, this file's hard gates are
the race->align invariant, the spot-checks, determinism, and catalog
structure -- NOT a brittle exact count, so ``len(IDENTITIES)`` is only
range-checked here.
"""

from __future__ import annotations

from nethackers.arena.seeds import trajectory_spec
from nethackers.contracts.models import (
    DEFAULT_MAX_STEPS,
    DEFAULT_NO_PROGRESS_TIMEOUT,
    ObjectiveSpec,
)
from nethackers.hub.objectives import (
    CATALOG,
    IDENTITIES,
    PUBLIC_SECRET,
    build_catalog,
    build_union_spec,
)

# Race -> legal alignments, transcribed independently of objectives.py (not
# imported from it) so this is a genuine check of the invariant against a
# second source, not a tautology against the module's own internal table.
_RACE_ALIGNMENTS = {
    "hum": {"law", "neu", "cha"},
    "elf": {"cha"},
    "dwa": {"law"},
    "gno": {"neu"},
    "orc": {"cha"},
}

_PRESENT = [
    "val-dwa-law-fem",
    "tou-hum-neu-mal",
    "wiz-elf-cha-mal",
    "sam-hum-law-mal",
    "pri-elf-cha-fem",
]
_ABSENT = [
    "val-elf-cha-mal",  # elf isn't a valid Valkyrie race
    "kni-dwa-law-mal",  # dwarf isn't a valid Knight race
    "tou-orc-cha-mal",  # orc isn't a valid Tourist race
    "sam-elf-cha-fem",  # elf isn't a valid Samurai race
]


def test_catalog_has_random_all_and_every_identity():
    assert "random" in CATALOG
    assert "all" in CATALOG
    assert set(IDENTITIES).issubset(CATALOG.keys())
    # Nothing stray: the catalog is exactly {random, all} + every identity.
    assert set(CATALOG.keys()) == set(IDENTITIES) | {"random", "all"}
    assert all(isinstance(spec, ObjectiveSpec) for spec in CATALOG.values())


def test_every_objectivespec_uses_the_arena_standard_knobs():
    for spec in CATALOG.values():
        assert spec.max_steps == DEFAULT_MAX_STEPS
        assert spec.no_progress_timeout == DEFAULT_NO_PROGRESS_TIMEOUT
        assert spec.action_timeout_seconds == 5.0


def test_batches_are_non_empty_int_str_pairs_except_all():
    # Brief step 1's "each batch non-empty" -- `all` is the documented
    # exception (task-4-context.md Resolution): it owns no episodes.
    for name, spec in CATALOG.items():
        if name == "all":
            continue
        assert len(spec.batch) > 0
        for seed, character in spec.batch:
            assert isinstance(seed, int)
            assert isinstance(character, str)


def test_random_batch_is_a_broad_deterministic_sample():
    spec = CATALOG["random"]
    assert spec.kind == "random"
    assert spec.aggregation == "asc_median_mean"
    assert len(spec.batch) == 32  # random_size default
    assert [seed for seed, _character in spec.batch] == list(range(32))

    characters = spec.characters()
    roles_seen = {c.split("-")[0] for c in characters}
    assert len(roles_seen) >= 2  # broad sample, spans multiple roles
    # A natural draw can never land on an illegal combo (e.g. a male
    # Valkyrie) -- every drawn character must itself be a legal identity.
    assert set(characters).issubset(set(IDENTITIES))


def test_random_batch_prefix_is_stable_across_random_size():
    # _natural_character(i) depends only on i, not on how many trajectories
    # were requested -- growing random_size should append, never reshuffle.
    small = build_catalog(random_size=5)["random"].batch
    big = build_catalog(random_size=32)["random"].batch
    assert big[:5] == small


def test_identity_objectives_are_single_character():
    # The brief's explicit sample.
    spec0 = CATALOG[IDENTITIES[0]]
    assert spec0.kind == "identity"
    assert spec0.aggregation == "mean"
    assert len(spec0.batch) == 15  # per_identity_size default
    assert set(spec0.characters()) == {IDENTITIES[0]}
    assert [seed for seed, _character in spec0.batch] == list(range(15))

    # Every identity, not just the sample -- cheap (73 x 15) and much
    # stronger coverage of the "single-character" invariant.
    for identity in IDENTITIES:
        spec = CATALOG[identity]
        assert spec.kind == "identity"
        assert spec.aggregation == "mean"
        assert spec.characters() == (identity,) * len(spec.batch)


def test_all_is_functional_with_an_empty_batch():
    spec = CATALOG["all"]
    assert spec.name == "all"
    assert spec.kind == "functional"
    assert spec.batch == ()
    assert spec.aggregation == "asc_median_mean"


def test_build_catalog_is_deterministic():
    first = build_catalog()
    second = build_catalog()
    assert {name: s.digest() for name, s in first.items()} == {
        name: s.digest() for name, s in second.items()
    }

    # Same non-default sizes, called twice, still agree.
    third = build_catalog(random_size=32, per_identity_size=8)
    fourth = build_catalog(random_size=32, per_identity_size=8)
    assert {name: s.digest() for name, s in third.items()} == {
        name: s.digest() for name, s in fourth.items()
    }

    # The module-level CATALOG matches a fresh default build too.
    assert {name: s.digest() for name, s in CATALOG.items()} == {
        name: s.digest() for name, s in first.items()
    }


def test_build_catalog_respects_custom_sizes():
    small = build_catalog(random_size=5, per_identity_size=2)
    assert len(small["random"].batch) == 5
    assert len(small[IDENTITIES[0]].batch) == 2
    assert small["all"].batch == ()


def test_race_alignment_invariant_holds_for_every_identity():
    for identity in IDENTITIES:
        role, race, align, gender = identity.split("-")
        assert align in _RACE_ALIGNMENTS[race], identity
        assert gender in ("fem", "mal")


def test_identity_spot_checks():
    for identity in _PRESENT:
        assert identity in IDENTITIES
    for identity in _ABSENT:
        assert identity not in IDENTITIES


def test_valkyrie_is_the_only_gender_locked_role():
    # NetHack 3.6.6 role.c: Valkyrie's roles[] entry carries ROLE_FEMALE but
    # never ROLE_MALE -- the game's sole role/race gender restriction (see
    # objectives.py's module docstring for the verified source citation).
    val_identities = [i for i in IDENTITIES if i.startswith("val-")]
    assert val_identities and all(i.endswith("-fem") for i in val_identities)
    # Every other role allows both genders somewhere in IDENTITIES.
    other_roles = {i.split("-")[0] for i in IDENTITIES} - {"val"}
    for role in other_roles:
        genders = {i.split("-")[3] for i in IDENTITIES if i.startswith(f"{role}-")}
        assert genders == {"fem", "mal"}, role


def test_identity_count_is_within_the_documented_range():
    # 73 <= len(IDENTITIES) <= 76: TNNT's canonical 73 valid role/race/
    # align/gender combos is the target; per task-4-context.md the range
    # (not a brittle exact count) is this test's hard gate.
    assert 73 <= len(IDENTITIES) <= 76


def test_random_batch_seeds_are_hmac_derived_from_the_public_secret():
    # Batches are HMAC-derived via PUBLIC_SECRET + trajectory_spec, not
    # arbitrary -- pin that the published seed int really is the
    # trajectory id, and that a different secret yields different
    # (unpublished) core seeds for the same trajectory id.
    spec = CATALOG["random"]
    for seed, _character in spec.batch:
        assert trajectory_spec(PUBLIC_SECRET, "catalog:random", seed).trajectory_id == seed
    mine = trajectory_spec(PUBLIC_SECRET, "catalog:random", 0)
    other = trajectory_spec("not-" + PUBLIC_SECRET, "catalog:random", 0)
    assert mine.core_seed != other.core_seed


def test_union_spec_is_concatenation_of_member_batches():
    members = ["wiz-elf-cha-mal", "wiz-orc-cha-mal"]
    spec = build_union_spec(members, name="wiz-pair")
    assert spec.kind == "set"
    assert spec.name == "wiz-pair"
    # each member contributes exactly its published batch, seeds 0..14
    for ident in members:
        member_pairs = [(s, c) for (s, c) in spec.batch if c == ident]
        assert member_pairs == list(CATALOG[ident].batch)
    assert len(spec.batch) == 15 * len(members)


def test_union_spec_single_member_equals_identity_batch():
    spec = build_union_spec(["wiz-elf-cha-mal"], name="wiz-elf-cha-mal")
    assert list(spec.batch) == list(CATALOG["wiz-elf-cha-mal"].batch)
