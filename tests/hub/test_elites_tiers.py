# tests/hub/test_elites_tiers.py
"""/elites?tier=verified reads the verified side-table, epoch-scoped."""

import pytest
from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig
from nethackers.hub.views.elites import read_elites
from nethackers.hub.views.source import Epoch

IDENT = "val-dwa-law-fem"
SECRET = "dev-secret"
SEEDS = (11, 22)


def _atom(digest, seed, progression, image=ARENA_IMAGE, tier="verified"):
    return Atom(
        solution_digest=digest, owner="sam", tier=tier, identity=IDENT, seed=seed,
        progression=progression, milestone="Dlvl:5", ascended=False,
        status="completed", turns=10, steps=20, evaluator_image=image,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    s.upsert_solution("sha256:a", repo="github.com/sam/n", commit_sha="a" * 40,
                      owner="sam", root=".", entrypoint="bot.py",
                      registered_at="2026-01-01T00:00:00Z")
    return s


def _epoch(store):
    from nethackers.arena.seeds import secret_fingerprint
    return Epoch(secret_fingerprint(SECRET), ARENA_IMAGE, SEEDS)


def test_verified_elites_rank_over_the_verified_table(store):
    store.insert_verified_atoms(
        [_atom("sha256:a", 11, 0.4), _atom("sha256:a", 22, 0.6)],
        secret_fingerprint=_epoch(store).secret_fingerprint,
        verifier_token_fingerprint="tok",
    )
    rows = read_elites(store, scope=IDENT, tier="verified", epoch=_epoch(store))
    assert len(rows) == 1
    assert rows[0]["identity"] == IDENT
    assert rows[0]["owner"] == "sam"
    assert rows[0]["score"] == pytest.approx(0.5)


def test_verified_elites_exclude_a_retired_seed(store):
    store.insert_verified_atoms(
        [_atom("sha256:a", 11, 0.4), _atom("sha256:a", 999, 1.0)],
        secret_fingerprint=_epoch(store).secret_fingerprint,
        verifier_token_fingerprint="tok",
    )
    rows = read_elites(store, scope=IDENT, tier="verified", epoch=_epoch(store))
    assert rows[0]["score"] == pytest.approx(0.4), "seed 999 is not in the epoch"


def test_verified_elites_ignore_self_reported_atoms(store):
    store.insert_atoms([_atom("sha256:a", 11, 0.9, tier="self-reported")])
    rows = read_elites(store, scope=IDENT, tier="verified", epoch=_epoch(store))
    assert rows == []


def _client(store, *, verifier):
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"}), verifier=verifier))


def test_route_returns_verified_rows(store):
    store.insert_verified_atoms(
        [_atom("sha256:a", 11, 0.4)],
        secret_fingerprint=_epoch(store).secret_fingerprint,
        verifier_token_fingerprint="tok",
    )
    cfg = VerifierConfig(tokens=frozenset({"tok"}), secret=SECRET, seeds=SEEDS)
    body = _client(store, verifier=cfg).get(f"/elites?scope={IDENT}&tier=verified").json()
    assert [r["owner"] for r in body["rows"]] == ["sam"]
    assert body["tier"] == "verified"


def test_route_503s_when_verification_is_not_configured(store):
    resp = _client(store, verifier=None).get(f"/elites?scope={IDENT}&tier=verified")
    assert resp.status_code == 503


def test_route_default_tier_is_still_self_reported(store):
    store.insert_atoms([_atom("sha256:a", 11, 0.9, tier="self-reported")])
    body = _client(store, verifier=None).get(f"/elites?scope={IDENT}").json()
    assert body["tier"] == "self-reported"
    assert len(body["rows"]) == 1
