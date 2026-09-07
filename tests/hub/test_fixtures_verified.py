"""The offline fixture set must populate the private tier, or `make hub`
serves an empty Private Dungeons view and there is nothing to e2e against.

The epoch has to line up exactly: the atoms are stamped with ARENA_IMAGE (the
real pinned arena the hub filters on -- NOT the fixtures' fake
_EVALUATOR_IMAGE) and inserted under sha256(DEV_HIDDEN_SECRET).

Follow-up regression guard: the fixture set must ALSO populate the PUBLIC
floor (``baseline_atoms``), or ``views.recognition``'s missing-floor
exclusion (Task 5) empties the public Frontier Keepers / Greatest
Breakthroughs tables outright -- confirmed against a real local hub, where
both tables rendered "No participant is above AutoAscend yet."."""

import pytest

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.hub.fixtures import (
    DEV_HIDDEN_SECRET,
    DEV_HIDDEN_SEEDS,
    IDENTITY_C,
    load_fixtures,
)
from nethackers.hub.store import Store
from nethackers.hub.views.baseline import read_baseline
from nethackers.hub.views.elites import read_elites
from nethackers.hub.views.recognition import read_recognition
from nethackers.hub.views.source import Epoch


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    load_fixtures(s)
    return s


def _epoch():
    return Epoch(secret_fingerprint(DEV_HIDDEN_SECRET), ARENA_IMAGE, DEV_HIDDEN_SEEDS)


def test_fixtures_populate_the_verified_tier(store):
    rows = read_elites(store, scope="generalist", tier="verified", epoch=_epoch())
    assert rows, "no verified elites -- the offline private tier would render empty"
    assert {r["owner"] for r in rows} <= {"howuhh", "vkurenkov", "vlomshakov", "cinemere"}


def test_fixtures_populate_the_verified_floor(store):
    body = read_baseline(store, tier="verified", epoch=_epoch())
    assert body["overall"] is not None
    assert body["per_identity"], "no verified AA floor -- every Delta would read '—'"


def test_fixtures_leave_one_identity_without_a_verified_floor(store):
    """The 5-identities-without-a-floor state exists in production, and it is
    where the 0.0-default bug lived. The offline set must reproduce it, or the
    exclusion path is never exercised by an e2e run."""
    elites = read_elites(store, scope="generalist", tier="verified", epoch=_epoch())
    floor = read_baseline(store, tier="verified", epoch=_epoch())["per_identity"]
    unfloored = {r["identity"] for r in elites} - set(floor)
    assert unfloored, "fixtures must include an identity with a result and no floor"


def test_verified_fixture_atoms_use_the_real_pinned_arena_image(store):
    atoms = store.iter_verified_atoms(secret_fingerprint=_epoch().secret_fingerprint)
    assert atoms
    assert {a.evaluator_image for a in atoms} == {ARENA_IMAGE}


def test_identity_c_specifically_has_no_verified_floor(store):
    # Named explicitly because the public-floor addition below inserts a
    # floor for IDENTITY_C on the PUBLIC tier -- this pins that the two
    # floor tables (baseline_atoms vs verified_baseline_atoms) stayed
    # isolated and IDENTITY_C's deliberate PRIVATE-tier floorless state
    # (test_fixtures_leave_one_identity_without_a_verified_floor, above)
    # wasn't accidentally undone by that addition.
    floor = read_baseline(store, tier="verified", epoch=_epoch())["per_identity"]
    assert IDENTITY_C not in floor


# --- Public tier (published seeds). Companion regression guard: without a
# public floor, views.recognition's missing-floor exclusion (Task 5) empties
# BOTH public ledgers on every freshly-built local hub -- confirmed against a
# real running hub, not just inferred from reading the code. ---


def test_fixtures_populate_the_public_floor(store):
    body = read_baseline(store)
    assert body["overall"] is not None
    assert body["per_identity"], (
        "no public AA floor -- every Delta reads '—' on the public tier too"
    )


def test_fixtures_public_recognition_renders_keepers_and_breakthroughs(store):
    # The actual regression guard: a floor that exists but sits too high
    # would reproduce the same empty-tables symptom just as silently as no
    # floor at all, so this asserts real rows, not merely a non-empty floor.
    body = read_recognition(store)
    assert body["keepers"], "no public keepers -- Frontier Keepers would render empty"
    assert body["breakthroughs"], (
        "no public breakthroughs -- Greatest Breakthroughs would render empty"
    )
