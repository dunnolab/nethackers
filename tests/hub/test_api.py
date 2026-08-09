"""Tests for ``nethackers.hub.api``: the FastAPI read + register HTTP
surface (M2a Task 12) -- thin handlers over the view/store/register
functions Tasks 5-11 already built. See task-12-context.md, which governs:
``fastapi.testclient.TestClient`` only (offline; no server, no NLE, no
Docker, no real network). ``POST /register`` wraps the request body's
``manifest``/``evidence.solution_digest`` in a ``LocalStubGit`` (M2a's
self-reported trust model) and delegates straight to ``validate.register``.

``IDENTITY``/``SPEC`` mirror test_validate.py's real-catalog-entry pattern:
the smallest real objective (a per-identity objective, 8 episodes) so the
register body built here has to satisfy the full validation ladder for
real, exactly like a real registration would.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store

IDENTITY = "val-dwa-law-fem"
SPEC = CATALOG[IDENTITY]  # a real, published identity objective (8 episodes)
BATCH_SIZE = len(SPEC.batch)

TOKEN = "tok-sam"
OWNER = "sam"

REPO = f"github.com/{OWNER}/nethacker"
COMMIT = "a" * 40  # a well-formed (if fake) 40-hex sha
DIGEST = "sha256:solution-a"
MANIFEST = {"root": ".", "entrypoint": "bot.py", "parents": [], "influences": []}


def _result(trajectory_id: int, character: str, **overrides: Any) -> TrajectoryResult:
    fields: dict[str, Any] = dict(
        trajectory_id=trajectory_id,
        status="completed",
        progress=0.02 + 0.01 * trajectory_id,
        ascended=False,
        steps=100 + trajectory_id,
        turns=90 + trajectory_id,
        max_depth=3,
        end_status=None,
        error=None,
        wall_seconds=1.0,
        character=character,
        milestone="Dlvl:3",
    )
    fields.update(overrides)
    return TrajectoryResult(**fields)


def _register_body() -> dict[str, Any]:
    # Exactly SPEC's published (seed, character) batch, in order -- so the
    # ladder's step-5 batch-match check passes for real.
    results = [_result(seed, character) for seed, character in SPEC.batch]
    evidence = Evidence.from_results(
        solution_digest=DIGEST,
        objective=Objective(character=None, seed_set=IDENTITY),
        evaluator_image="img@sha256:d",
        results=results,
        created_at="2026-01-01T00:00:00Z",
        tier="self-reported",
    )
    return {
        "reference": {"repo": REPO, "commit": COMMIT},
        "manifest": MANIFEST,
        "evidence": evidence.to_dict(),
    }


def _auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Any) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "hub.db")
    store.init_schema()
    app = create_app(store, LocalStubAuth({TOKEN: OWNER}))
    return TestClient(app), store


def test_register_then_read_solution(tmp_path: Any) -> None:
    # Property 1: POST /register with a stub token + a valid body -> 200,
    # RegisterResult.atoms_inserted == 8; GET /solutions/{digest} -> 200
    # with the stored solution.
    client, _store = _client(tmp_path)

    response = client.post("/register", json=_register_body(), headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["atoms_inserted"] == BATCH_SIZE == 8
    assert payload["solution_digest"] == DIGEST
    assert payload["owner"] == OWNER
    assert payload["objective"] == IDENTITY

    solution_response = client.get(f"/solutions/{DIGEST}")
    assert solution_response.status_code == 200
    solution = solution_response.json()
    assert solution["digest"] == DIGEST
    assert solution["owner"] == OWNER
    assert solution["repo"] == REPO
    assert solution["commit_sha"] == COMMIT


def test_attainment_non_empty_after_register(tmp_path: Any) -> None:
    # Property 2: GET /attainment -> 200, non-empty cells (the registered
    # atoms' Dlvl:3 milestone lit at least that identity's cells).
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    response = client.get("/attainment")

    assert response.status_code == 200
    cells = response.json()
    assert cells != []
    assert any(cell["identity"] == IDENTITY for cell in cells)


def test_elites_for_registered_objective(tmp_path: Any) -> None:
    # Property 3: GET /elites?objective=<that identity> -> 200, a list.
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    response = client.get("/elites", params={"objective": IDENTITY})

    assert response.status_code == 200
    entries = response.json()
    assert isinstance(entries, list)
    assert entries != []  # the just-registered solution ranks in its own identity


def test_board_random_objective_is_a_list(tmp_path: Any) -> None:
    # Property 4: GET /board?objective=random -> 200 (list, possibly empty
    # -- nothing was registered against "random" itself).
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    response = client.get("/board", params={"objective": "random"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_search_objectives_and_batch(tmp_path: Any) -> None:
    # Property 5: GET /search includes the registered solution; GET
    # /objectives lists the catalog; GET /objectives/{name}/batch returns
    # that identity's 8 published pairs; an unknown name 404s.
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    search_response = client.get("/search")
    assert search_response.status_code == 200
    digests = [row["digest"] for row in search_response.json()]
    assert DIGEST in digests

    objectives_response = client.get("/objectives")
    assert objectives_response.status_code == 200
    entries = {entry["name"]: entry for entry in objectives_response.json()}
    assert IDENTITY in entries
    assert entries[IDENTITY]["episodes"] == BATCH_SIZE
    assert "random" in entries

    batch_response = client.get(f"/objectives/{IDENTITY}/batch")
    assert batch_response.status_code == 200
    batch_payload = batch_response.json()
    assert batch_payload["name"] == IDENTITY
    assert len(batch_payload["batch"]) == BATCH_SIZE
    assert batch_payload["batch"][0] == [0, IDENTITY]

    unknown_response = client.get("/objectives/not-a-real-objective/batch")
    assert unknown_response.status_code == 404


def test_unknown_solution_digest_404(tmp_path: Any) -> None:
    # Property 6.
    client, _store = _client(tmp_path)

    response = client.get("/solutions/sha256:does-not-exist")

    assert response.status_code == 404


def test_register_bad_token_401(tmp_path: Any) -> None:
    # Property 7: a token the stub auth doesn't recognize -> 401, nothing
    # stored.
    client, store = _client(tmp_path)

    response = client.post("/register", json=_register_body(), headers=_auth_headers("bogus"))

    assert response.status_code == 401
    assert store.get_solution(DIGEST) is None


def test_board_unknown_objective_404_and_neither_param_400(tmp_path: Any) -> None:
    # Property 8.
    client, _store = _client(tmp_path)

    unknown = client.get("/board", params={"objective": "nope"})
    assert unknown.status_code == 404

    neither = client.get("/board")
    assert neither.status_code == 400


# --- Additional coverage beyond the context's enumerated 8 properties, per
# the task's self-review checklist (error-mapping completeness + /search's
# parametrized WHERE clause) -----------------------------------------------


def test_register_missing_authorization_header_401(tmp_path: Any) -> None:
    # No Authorization header at all -> 401, not a 422/500.
    client, store = _client(tmp_path)

    response = client.post("/register", json=_register_body())

    assert response.status_code == 401
    assert store.get_solution(DIGEST) is None


def test_register_wrong_owner_maps_to_403(tmp_path: Any) -> None:
    # The token resolves to "sam", but the reference repo is "other"'s ->
    # validate.WrongOwner -> 403 (not the generic 400).
    client, store = _client(tmp_path)
    body = _register_body()
    body["reference"]["repo"] = "github.com/other/nethacker"

    response = client.post("/register", json=body, headers=_auth_headers())

    assert response.status_code == 403
    assert store.get_solution(DIGEST) is None


def test_register_other_register_error_maps_to_400(tmp_path: Any) -> None:
    # evidence.objective.seed_set names no catalog objective ->
    # validate.UnknownObjective, a RegisterError that isn't WrongOwner ->
    # the generic 400 (not 401/403).
    client, store = _client(tmp_path)
    body = _register_body()
    body["evidence"]["objective"]["seed_set"] = "not-a-real-objective"

    response = client.post("/register", json=body, headers=_auth_headers())

    assert response.status_code == 400
    assert "UnknownObjective" in response.json()["detail"]
    assert store.get_solution(DIGEST) is None


def test_board_both_params_400(tmp_path: Any) -> None:
    client, _store = _client(tmp_path)

    response = client.get("/board", params={"objective": "random", "metric": "coverage"})

    assert response.status_code == 400


def test_board_metric_coverage_and_firsts(tmp_path: Any) -> None:
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    coverage = client.get("/board", params={"metric": "coverage"})
    assert coverage.status_code == 200
    assert isinstance(coverage.json(), list)

    firsts = client.get("/board", params={"metric": "firsts"})
    assert firsts.status_code == 200
    assert isinstance(firsts.json(), list)


def test_elites_unknown_objective_404(tmp_path: Any) -> None:
    client, _store = _client(tmp_path)

    response = client.get("/elites", params={"objective": "not-a-real-objective"})

    assert response.status_code == 404


def test_search_owner_filter(tmp_path: Any) -> None:
    client, _store = _client(tmp_path)
    client.post("/register", json=_register_body(), headers=_auth_headers())

    mine = client.get("/search", params={"owner": OWNER})
    assert mine.status_code == 200
    assert [row["digest"] for row in mine.json()] == [DIGEST]

    someone_elses = client.get("/search", params={"owner": "not-an-owner"})
    assert someone_elses.status_code == 200
    assert someone_elses.json() == []
