"""API tests for the hidden-seed AutoAscend floor: the token-gated
``POST /verify/baseline`` write, and the public ``baseline`` block that
``GET /verify/overview`` grows so the Verified board can show Delta-vs-AA."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

VTOKEN = "verifier-token-1"
CFG = VerifierConfig(tokens=frozenset({VTOKEN}), secret="hidden-key", seeds=(4839201, 1029384))
AUTH = {"Authorization": f"Bearer {VTOKEN}"}


def _app(tmp_path: Any, *, verifier=CFG) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "h.db")
    store.init_schema()

    class _Git:
        def commit_exists(self, repo: str, sha: str) -> bool: return True

    app = create_app(store, LocalStubAuth({"t": "sam"}),
                     git_factory=lambda token: _Git(), verifier=verifier)
    return TestClient(app), store


def _evidence_dict(identity="val-dwa-law-fem", progress=0.4, image=ARENA_IMAGE, seeds=None):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=progress,
                         ascended=False, steps=1, turns=1, max_depth=1, end_status="died",
                         error=None, wall_seconds=0.1, character=identity, milestone="Dlvl:3")
        for sd in (seeds if seeds is not None else CFG.seeds))
    return Evidence.from_results(
        solution_digest="autoascend", objective=Objective(character=None, seed_set="v"),
        evaluator_image=image, results=results, created_at="t").to_dict()


def _post(client, **kw):
    body = {"evidence": _evidence_dict(**kw),
            "secret_fingerprint": secret_fingerprint(CFG.secret)}
    return client.post("/verify/baseline", json=body, headers=AUTH)


def test_post_baseline_writes_the_floor_and_reports_coverage(tmp_path):
    client, store = _app(tmp_path)
    r = _post(client)
    assert r.status_code == 200
    assert r.json() == {"inserted": 2,
                        "coverage": {"done": 2, "total": len(IDENTITIES) * len(CFG.seeds)}}
    assert len(store.iter_verified_baseline_atoms()) == 2
    # The floor never becomes a participant.
    assert store.iter_atoms() == [] and store.iter_verified_atoms() == []
    assert store.get_solution("autoascend") is None


def test_post_baseline_requires_a_verifier_token(tmp_path):
    client, store = _app(tmp_path)
    body = {"evidence": _evidence_dict(), "secret_fingerprint": secret_fingerprint(CFG.secret)}
    assert client.post("/verify/baseline", json=body).status_code == 401
    assert client.post("/verify/baseline", json=body,
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    assert store.iter_verified_baseline_atoms() == []


def test_post_baseline_503_when_verification_unconfigured(tmp_path):
    client, _ = _app(tmp_path, verifier=None)
    assert _post(client).status_code == 503


def test_post_baseline_rejects_off_spec_evidence_with_400(tmp_path):
    client, store = _app(tmp_path)
    assert _post(client, image="somebody/arena:local").status_code == 400   # parity
    assert _post(client, seeds=(1, 2)).status_code == 400                   # batch
    assert store.iter_verified_baseline_atoms() == []


def test_post_baseline_rejects_non_finite_progress_with_400(tmp_path):
    """Sent as raw bytes, not via the JSON encoder: ``json.dumps`` refuses
    ``inf``, but ``json.loads`` accepts a bare ``Infinity`` literal, so this
    is what an off-spec body actually looks like on the wire."""
    client, store = _app(tmp_path)
    body = json.dumps({"evidence": _evidence_dict(),
                       "secret_fingerprint": secret_fingerprint(CFG.secret)})
    body = body.replace('"progress": 0.4', '"progress": Infinity')
    assert "Infinity" in body
    r = client.post("/verify/baseline", content=body.encode(),
                    headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400 and "NonFiniteMetrics" in r.text
    assert store.iter_verified_baseline_atoms() == []


def test_post_baseline_is_idempotent(tmp_path):
    client, store = _app(tmp_path)
    assert _post(client).json()["inserted"] == 2
    again = _post(client).json()
    assert again["inserted"] == 0 and again["coverage"]["done"] == 2
    assert len(store.iter_verified_baseline_atoms()) == 2


def test_overview_exposes_the_floor_publicly_without_a_token(tmp_path):
    """The floor is an aggregate, so it is public -- but it must never leak
    the hidden seed values themselves."""
    client, _ = _app(tmp_path)
    _post(client)
    body = client.get("/verify/overview").json()
    assert body["baseline"]["per_identity"]["val-dwa-law-fem"]["progression"] == 0.4
    assert body["baseline"]["overall"] == 0.4
    assert "4839201" not in client.get("/verify/overview").text


def test_overview_floor_is_empty_until_computed(tmp_path):
    client, _ = _app(tmp_path)
    body = client.get("/verify/overview").json()
    assert body["baseline"] == {"per_identity": {}, "overall": None}


def test_overview_floor_is_empty_when_unconfigured(tmp_path):
    client, _ = _app(tmp_path, verifier=None)
    assert client.get("/verify/overview").json()["baseline"] == {
        "per_identity": {}, "overall": None
    }


def test_overview_keeps_participants_and_floor_separate(tmp_path):
    """The whole point of the isolated table: submitting a floor must not
    move the participants' verified number, and vice versa."""
    client, _ = _app(tmp_path)
    _post(client)
    body = client.get("/verify/overview").json()
    assert body["per_identity"] == {} and body["overall"] is None
    assert body["baseline"]["overall"] == 0.4
