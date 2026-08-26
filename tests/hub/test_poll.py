"""Tests for nethackers.hub.poll: the frozen answer vocabularies + clean_vote
validation, plus a parity check that the client's key lists (web/index.html)
match the server's. Offline, pure stdlib."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nethackers.hub import poll
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.poll import PollValidationError, clean_vote
from nethackers.hub.store import Store

_HTML = (Path(__file__).parents[2] / "src/nethackers/hub/web/index.html").read_text("utf-8")


def _client_keys(name: str) -> set[str]:
    block = re.search(rf"const {name}=\[(.*?)\];", _HTML, re.S)
    assert block, f"const {name}=[...] not found in index.html"
    return set(re.findall(r'\["([^"]+)"', block.group(1)))


def test_vocab_parity_client_matches_server():
    assert _client_keys("METHOD") == poll.METHODS
    assert _client_keys("TIME") == poll.TIMELINES
    assert _client_keys("ROLE") == poll.ROLES
    assert _client_keys("XP") == poll.XPS


def test_clean_vote_accepts_and_normalizes():
    out = clean_vote(voter_id="abc", method="programs", timeline="2035",
                     roles=["player", "mlr", "player"], xp="ascended")
    assert out == {"voter_id": "abc", "method": "programs", "timeline": "2035",
                   "roles": ["mlr", "player"], "xp": "ascended"}  # deduped + sorted


def test_clean_vote_allows_empty_roles_and_null_xp():
    out = clean_vote(voter_id="a", method="never", timeline="never", roles=[], xp=None)
    assert out["roles"] == [] and out["xp"] is None


@pytest.mark.parametrize("kwargs", [
    dict(voter_id="", method="programs", timeline="2035", roles=[], xp=None),
    dict(voter_id="x" * 65, method="programs", timeline="2035", roles=[], xp=None),
    dict(voter_id="a", method="nope", timeline="2035", roles=[], xp=None),
    dict(voter_id="a", method="programs", timeline="1999", roles=[], xp=None),
    dict(voter_id="a", method="programs", timeline="2035", roles=[], xp="expert"),
    dict(voter_id="a", method="programs", timeline="2035", roles=["wizard"], xp=None),
])
def test_clean_vote_rejects_bad_fields(kwargs):
    with pytest.raises(PollValidationError):
        clean_vote(**kwargs)


def _client(tmp_path):
    store = Store(tmp_path / "h.db")
    store.init_schema()
    return TestClient(create_app(store, LocalStubAuth({}))), store


def test_get_poll_empty(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/poll")
    assert r.status_code == 200 and r.json() == {"votes": [], "total": 0}


def test_post_vote_persists_and_returns_aggregate(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/poll/vote", json={"voter_id": "v1", "method": "programs",
                    "timeline": "2035", "roles": ["mlr", "player"], "xp": "ascended"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["votes"][0] == {"method": "programs", "timeline": "2035",
                                "roles": ["mlr", "player"], "xp": "ascended"}
    assert "voter_id" not in body["votes"][0]  # anonymized


def test_post_vote_same_voter_replaces(tmp_path):
    client, _ = _client(tmp_path)
    client.post("/poll/vote", json={"voter_id": "v1", "method": "programs",
                "timeline": "2035", "roles": [], "xp": None})
    r = client.post("/poll/vote", json={"voter_id": "v1", "method": "hybrid",
                    "timeline": "2040", "roles": ["player"], "xp": "serious"})
    assert r.json()["total"] == 1  # still one row
    assert r.json()["votes"][0]["method"] == "hybrid"


def test_post_vote_rejects_bad_key(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/poll/vote", json={"voter_id": "v1", "method": "telepathy",
                    "timeline": "2035", "roles": [], "xp": None})
    assert r.status_code == 400
