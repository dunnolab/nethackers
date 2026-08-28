"""Tests for ``nethackers.hub.api``: the FastAPI read + link-only register
HTTP surface -- thin handlers over the view/store/register functions. Offline
only: ``fastapi.testclient.TestClient``, no server, no NLE, no Docker, and no
real network (``POST /register``'s commit-checker is a fake injected via
``git_factory``, so nothing ever calls GitHub).

The register write-path is now a clone-free ``repo@commit`` link ladder:
identity -> ownership -> 40-hex sha -> commit-exists. It writes no atoms, so
the score views (``/attainment``, ``/elites``, ``/board``) stay empty in M1 --
they still resolve and return lists, which is what these read tests assert.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.github import GitHubReadError
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store

TOKEN = "t"
OWNER = "sam"
REPO = f"github.com/{OWNER}/nethacker"
SHA = "b" * 40  # a well-formed (if fake) 40-hex sha

# A real, published identity objective, used by the read-endpoint tests.
IDENTITY = "val-dwa-law-fem"
BATCH_SIZE = len(CATALOG[IDENTITY].batch)

MANIFEST = {"root": "bot", "entrypoint": "bot.py"}


def _evidence_dict(objective_name: str = IDENTITY) -> dict[str, Any]:
    """A valid self-reported ``Evidence`` (as a JSON dict) whose
    ``(trajectory_id, character)`` set is exactly ``objective_name``'s
    published batch -- what ``POST /register`` carries in its body."""
    results = tuple(
        TrajectoryResult(
            trajectory_id=seed, status="completed", progress=0.5, ascended=False,
            steps=1, turns=1, max_depth=1, end_status="died", error=None,
            wall_seconds=0.1, character=character, milestone=None,
        )
        for seed, character in CATALOG[objective_name].batch
    )
    return Evidence.from_results(
        solution_digest="sha256:" + "ab" * 32,
        objective=Objective(character=None, seed_set=objective_name),
        evaluator_image="img", results=results, created_at="t",
    ).to_dict()


def _register_body(repo: str = REPO, commit: str = SHA) -> dict[str, Any]:
    """The ``{reference, manifest, evidence}`` register envelope the hub now
    expects (was ``{reference, root}``)."""
    return {
        "reference": {"repo": repo, "commit": commit},
        "manifest": MANIFEST,
        "evidence": _evidence_dict(),
    }


def _app(tmp_path: Any, exists: bool = True) -> tuple[TestClient, Store]:
    """A fresh app whose ``git_factory`` yields a fake commit-checker that
    reports every commit as ``exists`` -- no network. Returns ``(client,
    store)`` so tests can read the store back directly."""
    store = Store(tmp_path / "h.db")
    store.init_schema()

    class _Git:
        def commit_exists(self, repo: str, sha: str) -> bool:
            return exists

    app = create_app(store, LocalStubAuth({TOKEN: OWNER}), git_factory=lambda token: _Git())
    return TestClient(app), store


def _auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- healthz + register -----------------------------------------------------


def test_healthz(tmp_path: Any) -> None:
    # `auth` reports the provider's mode -- an ADDITIVE field (Task: offline
    # identity rename + effective-identity feature) so an old client that
    # only checks "status" is unaffected.
    client, _store = _app(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok", "auth": "offline"}


def test_healthz_reports_github_mode_for_a_real_auth_provider(tmp_path: Any) -> None:
    from nethackers.hub.auth import GitHubAppAuth

    store = Store(tmp_path / "h.db")
    store.init_schema()
    app = create_app(store, GitHubAppAuth("some-client-id"))
    client = TestClient(app)

    assert client.get("/healthz").json() == {"status": "ok", "auth": "github"}


def test_root_serves_the_page(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "NetHackers" in resp.text
    assert 'id="scBody"' in resp.text
    # The marquee count and the freshness stamp are live (JS from /stats); the
    # old hardcoded literals must never creep back into the served page. (The
    # full behavior lives in the manual jsdom harness tests/hub/web/wire.test.mjs.)
    assert "3 programs registered" not in resp.text
    assert 'id="updated"' in resp.text


def test_root_injects_the_real_package_version(tmp_path: Any) -> None:
    # The masthead version is stamped from the installed package at serve time,
    # so it never drifts from pyproject -- and no {{version}} token leaks through.
    from importlib.metadata import version

    client, _store = _app(tmp_path)
    body = client.get("/").text
    assert f"v{version('nethackers')}" in body
    assert "{{version}}" not in body


def test_stats_reads_empty(tmp_path: Any) -> None:
    # The sidebar counters are reachable over HTTP and zero on a fresh store.
    client, _store = _app(tmp_path)
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json() == {
        "programs": 0,
        "hackers": 0,
        "ascensions": 0,
        "identities_touched": 0,
        "best": 0.0,
        "last_registered_at": None,
    }


def test_register_link_ok(tmp_path: Any) -> None:
    # A valid link from its owner -> 200; the repo@commit link is stored.
    client, store = _app(tmp_path)

    response = client.post(
        "/register",
        json=_register_body(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["solution_id"] == f"{REPO}@{SHA}"
    assert payload["owner"] == OWNER

    row = store.get_solution(f"{REPO}@{SHA}")
    assert row is not None
    assert row["owner"] == OWNER
    assert row["commit_sha"] == SHA
    assert row["root"] == "bot"


def test_register_requires_token(tmp_path: Any) -> None:
    # No Authorization header at all -> 401 (from _bearer_token), nothing stored.
    client, store = _app(tmp_path)

    response = client.post("/register", json=_register_body())

    assert response.status_code == 401
    assert store.get_solution(f"{REPO}@{SHA}") is None


def test_register_wrong_owner_403(tmp_path: Any) -> None:
    # Token resolves to "sam" but the repo owner is "eve" -> WrongOwner -> 403.
    client, store = _app(tmp_path)

    response = client.post(
        "/register",
        json=_register_body(repo="github.com/eve/nethacker"),
        headers=_auth_headers(),
    )

    assert response.status_code == 403
    assert store.get_solution(f"github.com/eve/nethacker@{SHA}") is None


def test_register_bad_token_401(tmp_path: Any) -> None:
    # A present-but-unknown token -> LocalStubAuth raises AuthError -> 401.
    client, store = _app(tmp_path)

    response = client.post(
        "/register",
        json=_register_body(),
        headers=_auth_headers("bogus"),
    )

    assert response.status_code == 401
    assert store.get_solution(f"{REPO}@{SHA}") is None


def test_register_missing_commit_400(tmp_path: Any) -> None:
    # A well-formed sha the checker can't find -> MissingCommit (a
    # RegisterError) -> the generic 400.
    client, store = _app(tmp_path, exists=False)

    response = client.post(
        "/register",
        json=_register_body(),
        headers=_auth_headers(),
    )

    assert response.status_code == 400
    assert store.get_solution(f"{REPO}@{SHA}") is None


def test_register_github_error_502(tmp_path: Any) -> None:
    # The commit-checker raising GitHubReadError (e.g. a GitHub 5xx) -> 502.
    store = Store(tmp_path / "h.db")
    store.init_schema()

    class _RaisingGit:
        def commit_exists(self, repo: str, sha: str) -> bool:
            raise GitHubReadError("github commit lookup failed: 500")

    client = TestClient(
        create_app(store, LocalStubAuth({TOKEN: OWNER}), git_factory=lambda token: _RaisingGit())
    )

    response = client.post(
        "/register",
        json=_register_body(),
        headers=_auth_headers(),
    )

    assert response.status_code == 502
    assert store.get_solution(f"{REPO}@{SHA}") is None


# --- read endpoints (score views stay empty in M1, but still resolve) -------


def test_unknown_solution_digest_404(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    response = client.get("/solutions/sha256:does-not-exist")
    assert response.status_code == 404


def test_objectives_list_and_batch(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)

    objectives = client.get("/objectives")
    assert objectives.status_code == 200
    entries = {entry["name"]: entry for entry in objectives.json()}
    assert IDENTITY in entries
    assert entries[IDENTITY]["episodes"] == BATCH_SIZE
    assert "random" in entries

    batch = client.get(f"/objectives/{IDENTITY}/batch")
    assert batch.status_code == 200
    payload = batch.json()
    assert payload["name"] == IDENTITY
    assert len(payload["batch"]) == BATCH_SIZE
    assert payload["batch"][0] == [0, IDENTITY]

    unknown = client.get("/objectives/not-a-real-objective/batch")
    assert unknown.status_code == 404


def test_attainment_reads_empty(tmp_path: Any) -> None:
    # No atoms are ever written in M1, so attainment is an empty list.
    client, _store = _app(tmp_path)
    response = client.get("/attainment")
    assert response.status_code == 200
    assert response.json() == []


def test_elites_known_objective_is_a_list(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    response = client.get("/elites", params={"objective": IDENTITY})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_elites_unknown_objective_404(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    response = client.get("/elites", params={"objective": "not-a-real-objective"})
    assert response.status_code == 404


def test_board_reads_are_lists(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    for params in ({"objective": IDENTITY}, {"metric": "coverage"}, {"metric": "firsts"}):
        response = client.get("/board", params=params)
        assert response.status_code == 200
        assert isinstance(response.json(), list)


def test_board_unknown_objective_404_and_neither_param_400(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)

    unknown = client.get("/board", params={"objective": "nope"})
    assert unknown.status_code == 404

    neither = client.get("/board")
    assert neither.status_code == 400


def test_board_both_params_400(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    response = client.get("/board", params={"objective": IDENTITY, "metric": "coverage"})
    assert response.status_code == 400


def test_board_rejects_retired_random_and_all(tmp_path: Any) -> None:
    # random/all are retired (Task A1): every atom now lives on its
    # identity's own canonical batch, so neither is a board objective anymore.
    client, _store = _app(tmp_path)
    for token in ("random", "all"):
        assert client.get("/board", params={"objective": token}).status_code == 404


def test_search_lists_and_owner_filter(tmp_path: Any) -> None:
    # After registering a link, /search lists it and filters by owner. The
    # ``digest`` column now holds the repo@commit solution id.
    client, _store = _app(tmp_path)
    solution_id = f"{REPO}@{SHA}"

    registered = client.post(
        "/register",
        json=_register_body(),
        headers=_auth_headers(),
    )
    assert registered.status_code == 200

    listed = client.get("/search")
    assert listed.status_code == 200
    assert solution_id in [row["digest"] for row in listed.json()]

    mine = client.get("/search", params={"owner": OWNER})
    assert mine.status_code == 200
    assert [row["digest"] for row in mine.json()] == [solution_id]

    someone_elses = client.get("/search", params={"owner": "not-an-owner"})
    assert someone_elses.status_code == 200
    assert someone_elses.json() == []


def test_root_serves_the_dungeon_viz(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    body = client.get("/").text
    assert 'id="dictviz"' in body
    assert 'id="dictbar"' in body
    assert "/dictionary.mp3" in body
    # the dev-only window hook must be gone from the shipped page
    assert "__dictviz" not in body and "__dictSynth" not in body
    # regression: the hero "@" must stay UNIQUE on the canvas. The descent-glyph
    # ladder (DESC=[...]) must NOT contain "@" -- otherwise the wall renders many
    # stray @s that read as the hero duplicating (reported while scrolling).
    import re

    desc = re.search(r"const DESC=\[(.*?)\];", body, re.S)
    assert desc is not None, "DESC descent ladder not found in page"
    assert '"@"' not in desc.group(1), "hero glyph @ leaked into the DESC descent ladder"
    assert 'strokeText("@"' in body  # ...while the one true hero @ is still drawn


# --- leaderboard rework: aggregate boards + /hackers (Task 6) ---------------

VAL_IDS = ["val-dwa-law-fem", "val-hum-law-fem", "val-hum-neu-fem"]


def _seed_atoms(store: Store, atoms: list[Any], specs: list[Any]) -> None:
    for spec in specs:
        store.objectives_upsert(spec)
    for digest in {a.solution_digest for a in atoms}:
        store.upsert_solution(digest, repo="r", commit_sha="c", owner="sam",
                              root=".", entrypoint="bot.py", registered_at="t")
    store.insert_atoms(atoms)


def _mk_atom(**kw: Any) -> Any:
    from nethackers.contracts.models import Atom
    base: dict[str, Any] = dict(solution_digest="sha256:s",
                owner="dun", tier="self-reported", identity="val-dwa-law-fem", seed=0,
                progression=0.5, milestone=None, ascended=False, status="completed",
                turns=1, steps=1, evaluator_image="img")
    base.update(kw)
    return Atom(**base)


def test_board_generalist_returns_aggregate_shape(tmp_path: Any) -> None:
    client, store = _app(tmp_path)
    atoms = [_mk_atom(solution_digest="sha256:b", identity=i, seed=0, progression=0.2)
             for i in VAL_IDS]
    _seed_atoms(store, atoms, [CATALOG[i] for i in VAL_IDS])
    rows = client.get("/board?objective=generalist&tier=self-reported").json()
    assert rows and {"coverage", "total", "mean_progression"} <= set(rows[0])
    assert rows[0]["total"] == 73


def test_board_role_returns_aggregate_shape(tmp_path: Any) -> None:
    client, store = _app(tmp_path)
    atoms = [_mk_atom(solution_digest="sha256:b", identity=i, seed=0, progression=0.2)
             for i in VAL_IDS]
    _seed_atoms(store, atoms, [CATALOG[i] for i in VAL_IDS])
    rows = client.get("/board?objective=val").json()
    assert rows[0]["total"] == 3 and rows[0]["coverage"] == 3


def test_board_identity_includes_deepest(tmp_path: Any) -> None:
    client, store = _app(tmp_path)
    from nethackers.arena.progress import ACHIEVEMENTS
    atoms = [_mk_atom(solution_digest="sha256:s", seed=0,
                      progression=ACHIEVEMENTS["Dlvl:5"], milestone="Dlvl:5")]
    _seed_atoms(store, atoms, [CATALOG[IDENTITY]])
    rows = client.get(f"/board?objective={IDENTITY}").json()
    assert rows[0]["deepest"] == "Dlvl:5"


def test_board_unknown_objective_404(tmp_path: Any) -> None:
    client, _store = _app(tmp_path)
    assert client.get("/board?objective=nonsense").status_code == 404


def test_hackers_returns_union_shape_and_defaults_to_generalist(tmp_path: Any) -> None:
    client, store = _app(tmp_path)
    atoms = [
        _mk_atom(solution_digest="sha256:b", owner="dun",
                 identity=i, seed=0, progression=0.2)
        for i in VAL_IDS
    ]
    _seed_atoms(store, atoms, [CATALOG[i] for i in VAL_IDS])
    rows = client.get("/hackers").json()
    assert rows and {"owner", "coverage", "total", "mean_progression"} <= set(rows[0])
    assert rows[0]["total"] == 73 and rows[0]["owner"] == "dun"
