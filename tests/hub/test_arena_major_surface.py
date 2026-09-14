"""The major is visible to a reader, and the digests behind it are too."""

from typing import Any

from fastapi.testclient import TestClient

from nethackers.arena_version import ARENA_MAJOR, ARENA_MAJOR_BY_DIGEST
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

CFG = VerifierConfig(tokens=frozenset({"verifier-token-1"}), secret="hidden-key",
                     seeds=(4839201, 1029384))


def _app(tmp_path: Any, *, verifier=CFG) -> tuple[TestClient, Store]:
    """Copied from tests/hub/test_verify_api.py so this module stands alone."""
    store = Store(tmp_path / "h.db")
    store.init_schema()

    class _Git:
        def commit_exists(self, repo: str, sha: str) -> bool:
            return True

    app = create_app(store, LocalStubAuth({"t": "sam"}),
                     git_factory=lambda token: _Git(), verifier=verifier)
    return TestClient(app), store


def test_verified_board_envelope_carries_the_major(tmp_path):
    client, _ = _app(tmp_path)
    body = client.get("/board", params={"tier": "verified"}).json()
    assert body["arena_major"] == ARENA_MAJOR


def test_self_reported_board_has_no_major_key(tmp_path):
    client, _ = _app(tmp_path)
    body = client.get("/board").json()
    assert "arena_major" not in body


def test_overview_reports_the_major_and_its_digests(tmp_path):
    client, _ = _app(tmp_path)
    body = client.get("/verify/overview").json()
    assert body["arena_major"] == ARENA_MAJOR
    assert body["arena_digests"] == sorted(
        d for d, m in ARENA_MAJOR_BY_DIGEST.items() if m == ARENA_MAJOR
    )
