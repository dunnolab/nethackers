"""Tests for ``nethackers.hub.views.solution``: the per-solution,
per-identity frontier (the Program regime of the Frontier view). Unlike
``views.elites`` (community-wide, ranked across solutions) and
``views.attainment`` (community-wide, first-to-reach), this view is scoped
to a single ``solution_digest`` -- one solution's own mean progression on
each identity it has atoms for, ``AVG(progression) GROUP BY identity`` over
the ``atoms`` table.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.solution import read_solution_frontier

DIGEST = "sha256:solution-a"
OTHER_DIGEST = "sha256:solution-b"
EMPTY_DIGEST = "sha256:solution-empty"

# Two real identities the target digest has atoms on ("val-..." sorts
# before "wiz-..." -- exercises the view's ORDER BY identity for real).
WIZ_IDENTITY = "wiz-elf-cha-mal"
VAL_IDENTITY = "val-hum-neu-fem"
# A third real identity, distinct from both -- derived rather than
# hand-picked so it's guaranteed valid without re-deriving role/race/align
# legality by hand (mirrors test_elites.py's EXCLUDED_IDENTITY pattern).
OTHER_IDENTITY = next(i for i in IDENTITIES if i not in (WIZ_IDENTITY, VAL_IDENTITY))


def _atom(**overrides):
    identity = overrides.get("identity", WIZ_IDENTITY)
    fields = dict(
        solution_digest=DIGEST,
        owner="sam",
        tier="self-reported",
        identity=identity,
        seed=0,
        progression=0.5,
        milestone=None,
        ascended=False,
        status="completed",
        turns=5,
        steps=10,
        evaluator_image="img@sha256:x",
    )
    fields.update(overrides)
    return Atom(**fields)


def _new_store(tmp_path):
    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    return store


def _seed(store: Store, atoms: list[Atom]) -> None:
    """Register one ``solutions`` row per distinct solution (``insert_atoms``'
    remaining FK -- atoms no longer FK to ``objectives``, Task A3), then insert
    the atoms themselves. Safe to call more than once per test."""
    for digest in {atom.solution_digest for atom in atoms}:
        store.upsert_solution(
            digest,
            repo="r",
            commit_sha="c",
            owner="sam",
            root=".",
            entrypoint="bot.py",
            registered_at="2026-01-01T00:00:00Z",
        )
    store.insert_atoms(atoms)


def _frontier_atoms() -> list[Atom]:
    # DIGEST: two seeds on WIZ_IDENTITY (progression 0.4/0.6 -> mean 0.5,
    # episodes 2), one seed on VAL_IDENTITY (progression 0.2 -> mean 0.2,
    # episodes 1). OTHER_DIGEST: one seed on OTHER_IDENTITY -- a different
    # solution entirely, must never leak into DIGEST's frontier.
    return [
        _atom(identity=WIZ_IDENTITY, seed=0, progression=0.4),
        _atom(identity=WIZ_IDENTITY, seed=1, progression=0.6),
        _atom(identity=VAL_IDENTITY, seed=0, progression=0.2),
        _atom(solution_digest=OTHER_DIGEST, identity=OTHER_IDENTITY, seed=0, progression=0.9),
    ]


def test_read_solution_frontier_means_progression_per_identity_sorted(tmp_path):
    # Property 1: exactly the two identities DIGEST has atoms for come
    # back, sorted by identity, each with the mean progression and episode
    # count -- and OTHER_DIGEST's identity is absent.
    store = _new_store(tmp_path)
    _seed(store, _frontier_atoms())

    entries = read_solution_frontier(store, DIGEST)

    assert [e["identity"] for e in entries] == [VAL_IDENTITY, WIZ_IDENTITY]
    assert entries[0]["progression"] == pytest.approx(0.2)
    assert entries[0]["episodes"] == 1
    assert entries[1]["progression"] == pytest.approx(0.5)
    assert entries[1]["episodes"] == 2
    assert OTHER_IDENTITY not in [e["identity"] for e in entries]


def _client(tmp_path) -> tuple[TestClient, Store]:
    store = _new_store(tmp_path)
    app = create_app(store, LocalStubAuth({}))
    return TestClient(app), store


def test_get_solution_frontier_200_for_known_digest_404_for_unknown(tmp_path):
    # Property 2: GET /solutions/{digest}/frontier -> 200 with the same
    # rows read_solution_frontier computes, for a digest with atoms; ->
    # 404 (exact detail string, matching get_solution's) for a digest with
    # no registered solution at all.
    client, store = _client(tmp_path)
    _seed(store, _frontier_atoms())

    response = client.get(f"/solutions/{DIGEST}/frontier")

    assert response.status_code == 200
    entries = response.json()
    assert [e["identity"] for e in entries] == [VAL_IDENTITY, WIZ_IDENTITY]
    assert entries[0]["progression"] == pytest.approx(0.2)
    assert entries[0]["episodes"] == 1
    assert entries[1]["progression"] == pytest.approx(0.5)
    assert entries[1]["episodes"] == 2

    unknown = client.get("/solutions/sha256:does-not-exist/frontier")

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "unknown solution digest: 'sha256:does-not-exist'"


def test_solution_routes_accept_real_repo_commit_ids_with_slashes(tmp_path):
    client, store = _client(tmp_path)
    digest = "github.com/alice/nethacker@" + "a" * 40
    store.upsert_solution(
        digest,
        repo="github.com/alice/nethacker",
        commit_sha="a" * 40,
        owner="alice",
        root="autoascend",
        entrypoint="bot.py",
        registered_at="2026-01-01T00:00:00Z",
    )

    detail = client.get(f"/solutions/{digest}")
    frontier = client.get(f"/solutions/{digest}/frontier")

    assert detail.status_code == 200
    assert detail.json()["digest"] == digest
    assert frontier.status_code == 200
    assert frontier.json() == []


def test_get_solution_frontier_200_empty_list_for_known_solution_with_no_atoms(tmp_path):
    # Property 3 (spec's Testing section, frontier-view-design.md:139-140):
    # a solution that IS registered but has zero atoms -> 200 with [] --
    # distinct from the unknown-digest case above, which 404s. The view's
    # GROUP BY over zero matching rows already yields zero groups; this is
    # API-level coverage of that, not new behavior.
    client, store = _client(tmp_path)
    store.upsert_solution(
        EMPTY_DIGEST,
        repo="r",
        commit_sha="c",
        owner="sam",
        root=".",
        entrypoint="bot.py",
        registered_at="2026-01-01T00:00:00Z",
    )

    response = client.get(f"/solutions/{EMPTY_DIGEST}/frontier")

    assert response.status_code == 200
    assert response.json() == []
