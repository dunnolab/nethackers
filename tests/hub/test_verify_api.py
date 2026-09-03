from typing import Any

from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

VTOKEN = "verifier-token-1"
CFG = VerifierConfig(tokens=frozenset({VTOKEN}), secret="hidden-key", seeds=(4839201, 1029384))
REPO = "github.com/sam/nethacker"
SHA = "a" * 40


def _app(tmp_path: Any, *, verifier=CFG) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "h.db")
    store.init_schema()
    class _Git:
        def commit_exists(self, repo: str, sha: str) -> bool: return True
    app = create_app(store, LocalStubAuth({"t": "sam"}),
                     git_factory=lambda token: _Git(), verifier=verifier)
    return TestClient(app), store


def test_verify_config_returns_secret_and_seeds(tmp_path):
    client, _ = _app(tmp_path)
    r = client.get("/verify/config", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    assert r.json() == {"secret": "hidden-key", "seeds": [4839201, 1029384]}


def test_verify_config_rejects_bad_token(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/verify/config", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_verify_config_503_when_unconfigured(tmp_path):
    client, _ = _app(tmp_path, verifier=None)
    r = client.get("/verify/config", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 503


def _seed_solution(store):
    store.upsert_solution(f"{REPO}@{SHA}", repo=REPO, commit_sha=SHA, owner="sam",
                          root="bot", entrypoint="bot.py", registered_at="t")


def _evidence_dict(identity="val-dwa-law-fem"):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=identity, milestone=None)
        for sd in CFG.seeds)
    return Evidence.from_results(
        solution_digest=f"{REPO}@{SHA}", objective=Objective(character=None, seed_set="v"),
        evaluator_image=ARENA_IMAGE, results=results, created_at="t").to_dict()


def test_post_verify_ok(tmp_path):
    client, store = _app(tmp_path)
    _seed_solution(store)
    body = {"reference": {"repo": REPO, "commit": SHA}, "evidence": _evidence_dict(),
            "secret_fingerprint": secret_fingerprint(CFG.secret)}
    r = client.post("/verify", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    assert r.json()["inserted"] == 2
    assert len(store.iter_verified_atoms(solution_digest=f"{REPO}@{SHA}")) == 2


def test_post_verify_unknown_solution_404(tmp_path):
    client, _ = _app(tmp_path)
    body = {"reference": {"repo": REPO, "commit": SHA}, "evidence": _evidence_dict(),
            "secret_fingerprint": secret_fingerprint(CFG.secret)}
    r = client.post("/verify", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 404
