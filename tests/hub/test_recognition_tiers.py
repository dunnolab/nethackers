"""/recognition?tier=verified, and the missing-floor exclusion.

The `baseline.get(identity, 0.0)` default was invisible on the self-reported
tier (AutoAscend covers all 73 identities). On the verified tier some
identities have a program result and no floor yet, where that default would
assert "the floor is zero" -- inflating combined lift by the program's whole
score and claiming a breakthrough past 0.0."""

import pytest
from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.arena_version import ARENA_MAJOR
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig
from nethackers.hub.views.recognition import read_recognition
from nethackers.hub.views.source import Epoch

FLOORED = "val-dwa-law-fem"      # has a verified AA floor
UNFLOORED = "wiz-elf-cha-mal"    # program result, no verified AA floor yet
SECRET, SEEDS = "dev-secret", (11, 22)


def _atom(digest, owner, identity, seed, progression, tier="verified"):
    return Atom(
        solution_digest=digest, owner=owner, tier=tier, identity=identity,
        seed=seed, progression=progression, milestone="Dlvl:5", ascended=False,
        status="completed", turns=10, steps=20, evaluator_image=ARENA_IMAGE,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    s.upsert_solution("sha256:a", repo="github.com/sam/n", commit_sha="a" * 40,
                      owner="sam", root=".", entrypoint="bot.py",
                      registered_at="2026-01-01T00:00:00Z")
    fp = secret_fingerprint(SECRET)
    s.insert_verified_atoms(
        [_atom("sha256:a", "sam", FLOORED, 11, 0.50),
         _atom("sha256:a", "sam", FLOORED, 22, 0.50),
         _atom("sha256:a", "sam", UNFLOORED, 11, 0.70),
         _atom("sha256:a", "sam", UNFLOORED, 22, 0.70)],
        secret_fingerprint=fp, verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR,
    )
    s.insert_verified_baseline_atoms(
        [_atom("autoascend", "autoascend", FLOORED, 11, 0.20, tier="baseline"),
         _atom("autoascend", "autoascend", FLOORED, 22, 0.20, tier="baseline")],
        secret_fingerprint=fp, verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR,
    )
    return s


def _epoch():
    return Epoch(secret_fingerprint(SECRET), ARENA_MAJOR, SEEDS)


def test_verified_keepers_lift_is_measured_against_the_verified_floor(store):
    out = read_recognition(store, tier="verified", epoch=_epoch())
    assert len(out["keepers"]) == 1
    keeper = out["keepers"][0]
    assert keeper["owner"] == "sam"
    assert keeper["identities"] == [FLOORED], (
        f"{UNFLOORED} has no verified floor and must be excluded, not credited"
    )
    assert keeper["records"] == 1
    assert keeper["total_lift"] == pytest.approx(0.30)


def test_an_identity_with_no_floor_produces_no_breakthrough(store):
    out = read_recognition(store, tier="verified", epoch=_epoch())
    assert [b["identity"] for b in out["breakthroughs"]] == [FLOORED]
    assert all(b["previous"] != 0.0 for b in out["breakthroughs"])


def test_self_reported_recognition_is_unchanged(store):
    store.insert_atoms([_atom("sha256:a", "sam", FLOORED, 0, 0.9, tier="self-reported")])
    store.insert_baseline_atoms(
        [_atom("autoascend", "autoascend", FLOORED, 0, 0.1, tier="baseline")])
    out = read_recognition(store, tier="self-reported")
    assert out["keepers"][0]["total_lift"] == pytest.approx(0.8)
    assert set(out) == {"generated_at", "keepers", "breakthroughs"}


def _client(store, *, verifier):
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"}), verifier=verifier))


def test_route_tier_param(store):
    cfg = VerifierConfig(tokens=frozenset({"tok"}), secret=SECRET, seeds=SEEDS)
    client = _client(store, verifier=cfg)
    assert client.get("/recognition?tier=verified").json()["keepers"][0]["owner"] == "sam"
    assert client.get("/recognition").json()["keepers"] == []   # nothing self-reported
    assert '"seed"' not in client.get("/recognition?tier=verified").text


def test_route_503s_without_a_verifier(store):
    assert _client(store, verifier=None).get(
        "/recognition?tier=verified").status_code == 503
