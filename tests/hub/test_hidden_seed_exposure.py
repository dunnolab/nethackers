"""The hidden seeds are the whole basis of the private tier: a program that
learns them can train on them. /atoms returns raw per-atom rows INCLUDING
`seed`, so it must keep reading `atoms` directly -- where verified rows
structurally cannot appear -- and must never be routed through views.source.

This is a boundary, so it is asserted rather than assumed."""

import pytest
from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

IDENT = "val-dwa-law-fem"
SECRET = "dev-secret"
HIDDEN_SEEDS = (48151623, 42108642)


@pytest.fixture()
def client(tmp_path):
    s = Store(str(tmp_path / "hub.db"))
    s.init_schema()
    s.upsert_solution("sha256:a", repo="github.com/sam/n", commit_sha="a" * 40,
                      owner="sam", root=".", entrypoint="bot.py",
                      registered_at="2026-01-01T00:00:00Z")
    s.insert_verified_atoms(
        [Atom(solution_digest="sha256:a", owner="sam", tier="verified",
              identity=IDENT, seed=seed, progression=0.4, milestone="Dlvl:5",
              ascended=False, status="completed", turns=10, steps=20,
              evaluator_image=ARENA_IMAGE)
         for seed in HIDDEN_SEEDS],
        secret_fingerprint=secret_fingerprint(SECRET), verifier_token_fingerprint="tok",
        arena_major=1,
    )
    cfg = VerifierConfig(tokens=frozenset({"tok"}), secret=SECRET, seeds=HIDDEN_SEEDS)
    return TestClient(create_app(s, LocalStubAuth({"t": "sam"}), verifier=cfg))


@pytest.mark.parametrize("path", [
    "/atoms?tier=verified",
    "/atoms",
    f"/board?scope={IDENT}&tier=verified",
    "/board?scope=generalist&tier=verified",
    f"/elites?scope={IDENT}&tier=verified",
    "/baseline?tier=verified",
    "/recognition?tier=verified",
    "/verify/overview",
])
def test_no_public_route_leaks_a_hidden_seed(client, path):
    body = client.get(path).text
    for seed in HIDDEN_SEEDS:
        assert str(seed) not in body, f"{path} leaked hidden seed {seed}"


def test_atoms_tier_verified_stays_empty(client):
    # Not "returns verified rows without seeds" -- returns NOTHING. /atoms reads
    # the `atoms` table, which verified rows never enter.
    assert client.get("/atoms?tier=verified").json()["rows"] == []


def test_the_brief_routes_never_carry_a_hidden_seed(client):
    """The brief reads the verified tier for its floor. Aggregates only."""
    for path, headers in (("/", {"Accept": "text/markdown, text/html, */*"}),
                          ("/index.md", {}), ("/llms.txt", {})):
        body = client.get(path, headers=headers).text
        for seed in HIDDEN_SEEDS:
            assert str(seed) not in body
