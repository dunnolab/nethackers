from fastapi.testclient import TestClient

from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.ids import program_id
from nethackers.hub.store import Store

DIGEST = "github.com/o/r@abc123"


def _client(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    store.init_schema()
    store.upsert_solution(DIGEST, repo="github.com/o/r", commit_sha="abc123",
                          owner="sam", root=".", entrypoint="bot.py",
                          registered_at="2026-01-01T00:00:00Z")
    store.insert_atoms([Atom(solution_digest=DIGEST, owner="sam", tier="self-reported",
                             identity="val-hum-neu-fem", seed=0, progression=0.5,
                             milestone=None, ascended=False, status="completed",
                             turns=5, steps=10, evaluator_image="img@sha256:x")])
    return TestClient(create_app(store, LocalStubAuth({}))), store


def test_atoms_export_filters_by_program_and_carries_program_id(tmp_path):
    client, _ = _client(tmp_path)
    pid = program_id(DIGEST)
    r = client.get(f"/atoms?program={pid}")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["identity"] == "val-hum-neu-fem"
    assert rows[0]["program_id"] == pid
    assert client.get("/atoms?program=prog_missing").json()["rows"] == []
