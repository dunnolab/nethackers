# tests/hub/test_boards_tiers.py
"""/board?tier=verified ranks over verified_atoms, same row shape as public."""

import pytest
from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.arena_version import ARENA_MAJOR
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

IDENT = "val-dwa-law-fem"
SECRET, SEEDS = "dev-secret", (11, 22)


def _atom(digest, owner, seed, progression, tier="verified"):
    return Atom(
        solution_digest=digest, owner=owner, tier=tier, identity=IDENT, seed=seed,
        progression=progression, milestone="Dlvl:5", ascended=False,
        status="completed", turns=10, steps=20, evaluator_image=ARENA_IMAGE,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    for digest, owner in (("sha256:a", "sam"), ("sha256:b", "kim")):
        s.upsert_solution(digest, repo=f"github.com/{owner}/n", commit_sha="a" * 40,
                          owner=owner, root=".", entrypoint="bot.py",
                          registered_at="2026-01-01T00:00:00Z")
    s.insert_verified_atoms(
        [_atom("sha256:a", "sam", 11, 0.2), _atom("sha256:a", "sam", 22, 0.4),
         _atom("sha256:b", "kim", 11, 0.8), _atom("sha256:b", "kim", 22, 0.6)],
        secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
        arena_major=ARENA_MAJOR,
    )
    return s


def _client(store, *, verifier):
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"}), verifier=verifier))


def _cfg():
    return VerifierConfig(tokens=frozenset({"tok"}), secret=SECRET, seeds=SEEDS)


def test_verified_board_ranks_by_verified_mean(store):
    body = _client(store, verifier=_cfg()).get(f"/board?scope={IDENT}&tier=verified").json()
    rows = body["rows"]
    assert [r["owner"] for r in rows] == ["kim", "sam"]
    assert rows[0]["mean_progression"] == pytest.approx(0.7)
    assert rows[1]["mean_progression"] == pytest.approx(0.3)


def test_verified_board_rows_carry_the_same_fields_as_public(store):
    row = _client(store, verifier=_cfg()).get(
        f"/board?scope={IDENT}&tier=verified").json()["rows"][0]
    for key in ("rank", "program_id", "owner", "reference", "registered_at",
                "coverage", "identities_total", "ascensions",
                "mean_progression", "median_progression", "deepest"):
        assert key in row, f"verified board row is missing {key!r}"
    assert row["reference"]["repo"] == "github.com/kim/n"
    assert row["registered_at"] == "2026-01-01T00:00:00Z"


def test_verified_board_never_exposes_a_seed(store):
    # Shape check only -- board rows are per-program aggregates and carry no
    # seed field at all. Task 6 scans for the seed VALUES across every route,
    # using large distinctive seeds that can't collide with a progression.
    text = _client(store, verifier=_cfg()).get(f"/board?scope={IDENT}&tier=verified").text
    assert '"seed"' not in text, "hidden seeds must never reach a public board"


def test_generalist_verified_board_aggregates(store):
    body = _client(store, verifier=_cfg()).get("/board?scope=generalist&tier=verified").json()
    assert [r["owner"] for r in body["rows"]] == ["kim", "sam"]
    assert body["rows"][0]["identities_total"] == 73
    assert body["rows"][0]["coverage"] == 1


def test_verified_board_503s_without_a_verifier(store):
    assert _client(store, verifier=None).get(
        f"/board?scope={IDENT}&tier=verified").status_code == 503
