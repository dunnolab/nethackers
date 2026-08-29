# tests/hub/test_hackers_leaders.py
from fastapi.testclient import TestClient
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store


def _client(tmp_path):
    store = Store(tmp_path / "h.sqlite3"); store.init_schema()
    store.upsert_solution("github.com/o/r@c", repo="github.com/o/r", commit_sha="c",
                          owner="sam", root=".", entrypoint="bot.py",
                          registered_at="2026-01-01T00:00:00Z")
    # sam scores on a Valkyrie identity (role val).
    store.insert_atoms([Atom(solution_digest="github.com/o/r@c", owner="sam",
                             tier="self-reported", identity="val-hum-neu-fem", seed=0,
                             progression=0.7, milestone=None, ascended=False,
                             status="completed", turns=5, steps=10,
                             evaluator_image="img@sha256:x")])
    return TestClient(create_app(store, LocalStubAuth({}))), store


def test_leaders_by_role_returns_best_hacker_per_value(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/hackers/leaders?by=role")
    assert r.status_code == 200
    body = r.json()
    assert body["by"] == "role"
    val = next(row for row in body["rows"] if row["value"] == "val")
    assert val["owner"] == "sam"
    assert val["score"] == 0.7
    assert "coverage" in val
    # roles with no atoms are omitted, not null rows
    assert all(row["owner"] for row in body["rows"])


def test_leaders_unknown_facet_is_400(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/hackers/leaders?by=bogus").status_code == 400
