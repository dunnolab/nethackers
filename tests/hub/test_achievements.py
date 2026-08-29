from fastapi.testclient import TestClient
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.ids import program_id
from nethackers.hub.store import Store

DIGEST = "github.com/o/r@abc123"


def _client(tmp_path):
    store = Store(tmp_path / "h.sqlite3"); store.init_schema()
    store.upsert_solution(DIGEST, repo="github.com/o/r", commit_sha="abc123",
                          owner="sam", root=".", entrypoint="bot.py",
                          registered_at="2026-01-01T00:00:00Z")
    # An atom that reaches a milestone so the attainment tables get populated.
    store.insert_atoms([Atom(solution_digest=DIGEST, owner="sam", tier="self-reported",
                             identity="val-hum-neu-fem", seed=0, progression=0.5,
                             milestone="Dlvl:5", ascended=False, status="completed",
                             turns=5, steps=10, evaluator_image="img@sha256:x")])
    return TestClient(create_app(store, LocalStubAuth({}))), store


def test_achievements_routes_envelope_and_remap_ids(tmp_path):
    client, _ = _client(tmp_path)
    pid = program_id(DIGEST)

    m = client.get("/achievements/milestones?identity=val-hum-neu-fem")
    assert m.status_code == 200
    body = m.json()
    assert "generated_at" in body
    for row in body["rows"]:
        assert "first_program_id" in row and "first_solution" not in row
        assert row["first_program_id"] == pid

    for path, count_key in (("/achievements/coverage", "cells_held"),
                            ("/achievements/firsts", "firsts")):
        resp = client.get(path)
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert "program_id" in row and "solution_digest" not in row
            assert count_key in row
