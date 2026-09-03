from typing import Any

from fastapi.testclient import TestClient

from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

VTOKEN = "verifier-token-1"
CFG = VerifierConfig(tokens=frozenset({VTOKEN}), secret="hidden-key", seeds=(4839201, 1029384))


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
