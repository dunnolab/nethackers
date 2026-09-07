"""/baseline?tier=verified returns the hidden-seed AutoAscend floor, and is the
same measurement /verify/overview reports as its `baseline` key."""

import pytest
from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

IDENT = "val-dwa-law-fem"
SECRET, SEEDS = "dev-secret", (11, 22)


def _aa(seed, progression, image=ARENA_IMAGE):
    return Atom(
        solution_digest="autoascend", owner="autoascend", tier="baseline",
        identity=IDENT, seed=seed, progression=progression, milestone="Dlvl:4",
        ascended=False, status="completed", turns=10, steps=20,
        evaluator_image=image,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    s.insert_baseline_atoms([_aa(0, 0.9)])            # published-seed floor
    s.insert_verified_baseline_atoms(                  # hidden-seed floor
        [_aa(11, 0.1), _aa(22, 0.3)],
        secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
    )
    return s


def _client(store, *, verifier):
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"}), verifier=verifier))


def _cfg():
    return VerifierConfig(tokens=frozenset({"tok"}), secret=SECRET, seeds=SEEDS)


def test_public_baseline_is_unchanged(store):
    body = _client(store, verifier=_cfg()).get("/baseline").json()
    assert body["owner"] == "autoascend"
    assert body["per_identity"][IDENT]["progression"] == pytest.approx(0.9)


def test_verified_baseline_reads_the_hidden_floor(store):
    body = _client(store, verifier=_cfg()).get("/baseline?tier=verified").json()
    assert body["per_identity"][IDENT]["progression"] == pytest.approx(0.2)
    assert body["per_identity"][IDENT]["episodes"] == 2
    assert body["overall"] == pytest.approx(0.2)


def test_verified_baseline_matches_verify_overview(store):
    client = _client(store, verifier=_cfg())
    direct = client.get("/baseline?tier=verified").json()
    overview = client.get("/verify/overview").json()["baseline"]
    assert direct["per_identity"] == overview["per_identity"]
    assert direct["overall"] == overview["overall"]


def test_verified_baseline_never_exposes_a_seed(store):
    assert '"seed"' not in _client(store, verifier=_cfg()).get(
        "/baseline?tier=verified").text


def test_verified_baseline_503s_without_a_verifier(store):
    assert _client(store, verifier=None).get(
        "/baseline?tier=verified").status_code == 503
