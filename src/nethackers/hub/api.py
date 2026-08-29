"""Hub FastAPI app: the thin HTTP surface over the read views and the
link-only register write-path. Nothing in this module runs candidate code,
computes a score, or does anything ``store``/``auth``/the register ladder
don't already do -- every handler just parses its request, delegates, and
maps the result/exception to an HTTP response.

``create_app(store, auth, *, catalog=CATALOG, git_factory=...)`` builds a
fresh ``FastAPI`` app with every handler a closure over its own
``store``/``auth``/``catalog``/``git_factory`` -- no module-level app
singleton, so each caller (each test) gets an isolated instance. Reads hit
the view/store functions directly. ``POST /register`` extracts the caller's
Bearer token, builds a commit-checker from it via ``git_factory`` (a real
``GitHubRead`` by default; tests inject a fake), and delegates to
``validate.register`` -- the clone-free identity+ownership+commit-exists
ladder -- mapping its exceptions to HTTP status codes (``AuthError`` -> 401,
``WrongOwner`` -> 403, ``RegisterError`` -> 400, ``GitHubReadError`` -> 502).
``GET /healthz`` is an unauthenticated liveness probe that also reports the
hub's ``auth`` mode (``"offline"``/``"github"``, from the ``AuthProvider``'s
own ``.mode``) -- an additive field a client uses to show effective identity
(``hubclient.client.HubClient.hub_mode``) instead of a bare 401 on register.
The hub holds no GitHub secret: every GitHub read uses the caller's own
token.

**Catalog-injection scope:** the injected ``catalog`` drives only this
module's own listing/resolution endpoints -- ``GET /objectives``,
``GET /objectives/{name}/batch``, and the name->spec resolution
``GET /board``'s ``?objective=`` branch needs. ``GET /elites`` delegates
straight to ``views.elites.read_elites``, which resolves against the
*module* ``nethackers.hub.objectives.CATALOG`` -- not whatever ``catalog``
this app was built with. In normal use (the default ``catalog=CATALOG``)
every endpoint agrees, since both are the same dict object.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.hub.auth import AuthError, AuthProvider, GitHubAppAuth, LocalStubAuth
from nethackers.hub.envelope import envelope
from nethackers.hub.github import GitHubRead, GitHubReadError
from nethackers.hub.ids import program_id
from nethackers.hub.objectives import CATALOG
from nethackers.hub.poll import PollValidationError, clean_vote
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    CommitChecker,
    RegisterError,
    SolutionReference,
    WrongOwner,
    register,
)
from nethackers.hub.views.achievements import coverage as achievements_coverage
from nethackers.hub.views.achievements import firsts as achievements_firsts
from nethackers.hub.views.achievements import milestones as achievements_milestones
from nethackers.hub.views.attainment import read_attainment
from nethackers.hub.views.baseline import read_baseline
from nethackers.hub.views.boards import (
    aggregate_board,
    board,
    coverage_board,
    firsts_board,
    resolve_scope,
)
from nethackers.hub.views.elites import read_elites
from nethackers.hub.views.hackers import hacker_board
from nethackers.hub.views.progress import read_progress
from nethackers.hub.views.programs import get_program, list_programs
from nethackers.hub.views.solution import read_solution_frontier
from nethackers.hub.views.stats import read_stats

# The index.html file shipped in the wheel package data.
_INDEX = Path(__file__).parent / "web" / "index.html"


def _dict_audio_path() -> Path:
    """The optional background track path, read from NETHACKERS_DICT_AUDIO env
    (default: the package web/dictionary.mp3). Read at request time so tests can
    monkeypatch without module reload."""
    return Path(os.environ.get(
        "NETHACKERS_DICT_AUDIO",
        str(Path(__file__).parent / "web" / "dictionary.mp3"),
    ))

# GET /search's column list, local to this module -- the context calls for
# keeping this one small SELECT in api.py rather than adding a store.py
# method for it.
_SEARCH_COLUMNS: tuple[str, ...] = (
    "digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at",
)


class RegisterRequest(BaseModel):
    """The ``POST /register`` envelope: a ``repo@commit`` link. ``reference``
    is kept as a raw ``{repo, commit}`` dict -- the handler parses it by hand
    (``SolutionReference(**...)``) rather than mirroring its shape as a nested
    Pydantic model, since the ``SolutionReference`` dataclass in ``validate.py``
    is already the source of truth for it. ``root`` is the optional
    subdirectory the solution lives under (default: the repo root)."""

    reference: dict[str, str]
    manifest: dict[str, Any]
    evidence: dict[str, Any]


class PollVoteRequest(BaseModel):
    """POST /poll/vote body. roles is multi-select (0-4 keys); xp may be null."""

    voter_id: str
    method: str
    timeline: str
    roles: list[str] = []
    xp: str | None = None


def _bearer_token(authorization: str | None) -> str:
    """The token out of ``Authorization: Bearer <token>``; raises a 401
    ``HTTPException`` if the header is missing or isn't that scheme --
    before ``auth.resolve`` (via ``register``) ever sees it."""
    if authorization is not None:
        scheme, _sep, token = authorization.partition(" ")
        if scheme == "Bearer" and token:
            return token
    raise HTTPException(status_code=401, detail="missing or malformed Authorization header")


def create_app(
    store: Store,
    auth: AuthProvider,
    *,
    catalog: dict[str, ObjectiveSpec] = CATALOG,
    git_factory: Callable[[str], CommitChecker] = lambda token: GitHubRead(token),
) -> FastAPI:
    """Build a hub API app over ``store``/``auth``. Every route is a closure
    over ``store``/``auth``/``catalog``/``git_factory`` -- see module
    docstring for the catalog-injection scope. ``git_factory`` maps the
    caller's Bearer token to a commit-checker (default: a real ``GitHubRead``;
    tests inject a fake). Reads delegate straight to the view/store functions;
    nothing here executes candidate code."""
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # `auth` is additive (offline-identity/effective-identity feature): an
        # old client that only reads "status" is unaffected; a new client
        # reads it to show the hub's effective identity instead of a bare 401
        # on register with no hint why (see hubclient.client.HubClient.hub_mode).
        return {"status": "ok", "auth": auth.mode}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        # Stamp the masthead {{version}} from the installed package at serve
        # time, so it can never drift from pyproject the way a hardcoded
        # string does. Read per-request (like _dict_audio_path) -- cheap, and
        # keeps the handler a pure function of the file + package metadata.
        return _INDEX.read_text(encoding="utf-8").replace(
            "{{version}}", _pkg_version("nethackers")
        )

    @app.get("/poll")
    def poll() -> dict[str, Any]:
        votes = store.iter_poll_votes()
        return {"votes": votes, "total": len(votes)}

    @app.post("/poll/vote")
    def poll_vote(body: PollVoteRequest) -> dict[str, Any]:
        try:
            vote = clean_vote(
                voter_id=body.voter_id, method=body.method, timeline=body.timeline,
                roles=body.roles, xp=body.xp,
            )
        except PollValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        store.upsert_poll_vote(
            vote["voter_id"], method=vote["method"], timeline=vote["timeline"],
            roles=vote["roles"], xp=vote["xp"],
        )
        votes = store.iter_poll_votes()
        return {"votes": votes, "total": len(votes)}

    @app.get("/dictionary.mp3")
    def dictionary_audio() -> FileResponse:
        path = _dict_audio_path()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no background track")
        return FileResponse(path, media_type="audio/mpeg")

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return read_stats(store)

    @app.get("/baseline")
    def baseline() -> dict[str, Any]:
        return read_baseline(store)

    @app.get("/progress")
    def progress(objective: str | None = None, tier: str = "self-reported") -> dict[str, Any]:
        return read_progress(store, objective=objective, tier=tier)

    @app.get("/objectives")
    def list_objectives() -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "kind": spec.kind,
                "aggregation": spec.aggregation,
                "episodes": len(spec.batch),
            }
            for spec in catalog.values()
        ]

    @app.get("/objectives/{name}/batch")
    def objective_batch(name: str) -> dict[str, Any]:
        spec = catalog.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"unknown objective: {name!r}")
        return {"name": name, "batch": [[seed, character] for seed, character in spec.batch]}

    @app.get("/attainment")
    def attainment(identity: str | None = None) -> list[dict[str, Any]]:
        return read_attainment(store, identity=identity)

    @app.get("/achievements/milestones")
    def achievements_milestones_route(identity: str | None = None) -> dict[str, Any]:
        return envelope(achievements_milestones(store, identity=identity), identity=identity)

    @app.get("/achievements/coverage")
    def achievements_coverage_route() -> dict[str, Any]:
        return envelope(achievements_coverage(store))

    @app.get("/achievements/firsts")
    def achievements_firsts_route() -> dict[str, Any]:
        return envelope(achievements_firsts(store))

    @app.get("/elites")
    def elites(objective: str) -> list[dict[str, Any]]:
        if objective not in catalog:
            raise HTTPException(status_code=404, detail=f"unknown objective: {objective!r}")
        return read_elites(store, objective=objective)

    @app.get("/board")
    def get_board(
        objective: str | None = None,
        metric: str | None = None,
        tier: str = "self-reported",
    ) -> list[dict[str, Any]]:
        if (objective is None) == (metric is None):
            raise HTTPException(
                status_code=400, detail="specify exactly one of ?objective= or ?metric="
            )
        if objective is not None:
            # only identities are single-objective boards; random/all are
            # retired (Task A1) and generalist/role are always a
            # macro-average over their identity set.
            if objective in catalog and catalog[objective].kind == "identity":
                return board(store, catalog[objective], tier=tier)
            try:
                _kind, ids = resolve_scope(objective)
            except ValueError as e:
                raise HTTPException(
                    status_code=404, detail=f"unknown objective: {objective!r}"
                ) from e
            return aggregate_board(store, ids, tier=tier)
        if metric == "coverage":
            return coverage_board(store)
        if metric == "firsts":
            return firsts_board(store)
        raise HTTPException(status_code=400, detail=f"unknown metric: {metric!r}")

    @app.get("/hackers")
    def hackers(
        objective: str = "generalist", tier: str = "self-reported"
    ) -> list[dict[str, Any]]:
        try:
            _kind, ids = resolve_scope(objective)
        except ValueError as e:
            raise HTTPException(
                status_code=404, detail=f"unknown objective: {objective!r}"
            ) from e
        return hacker_board(store, ids, tier=tier)

    @app.get("/hackers/random")
    def hackers_random(n: int = 20) -> list[str]:
        """Up to ``n`` (clamped to 20) random registered hacker handles,
        sampled server-side -- names for the dungeon-wall @username runners.
        Cheaper than /hackers: no coverage/mean aggregation."""
        return store.random_owners(max(0, min(20, n)))

    # ``:path`` (not the default converter) so a link-registered digest --
    # ``github.com/owner/repo@commit``, which carries slashes -- matches; the
    # default one only spans a single path segment, 404ing such a digest at
    # routing before the handler runs (this is what broke the Frontier
    # "Program" tab). The greedy ``:path`` means the ``/frontier`` route must be
    # registered BEFORE the bare one, else it swallows the ``/frontier`` suffix.
    @app.get("/solutions/{digest:path}/frontier")
    def solution_frontier(digest: str) -> list[dict[str, Any]]:
        if store.get_solution(digest) is None:
            raise HTTPException(status_code=404, detail=f"unknown solution digest: {digest!r}")
        return read_solution_frontier(store, digest)

    @app.get("/solutions/{digest:path}")
    def get_solution(digest: str) -> dict[str, Any]:
        solution = store.get_solution(digest)
        if solution is None:
            raise HTTPException(status_code=404, detail=f"unknown solution digest: {digest!r}")
        return solution

    @app.get("/search")
    def search(owner: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        columns_sql = ", ".join(_SEARCH_COLUMNS)
        sql = f"SELECT {columns_sql} FROM solutions"
        params: list[Any] = []
        if owner is not None:
            sql += " WHERE owner = ?"
            params.append(owner)
        sql += " ORDER BY registered_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = store.conn.execute(sql, params).fetchall()
        return [dict(zip(_SEARCH_COLUMNS, row, strict=True)) for row in rows]

    @app.get("/programs")
    def programs(owner: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return envelope(list_programs(store, owner=owner, limit=limit, offset=offset), owner=owner)

    @app.get("/programs/{program_id}/identities")
    def program_identities(program_id: str) -> dict[str, Any]:
        digest = store.digest_for_program_id(program_id)
        if digest is None:
            raise HTTPException(status_code=404, detail=f"unknown program id: {program_id!r}")
        return envelope(read_solution_frontier(store, digest), program_id=program_id)

    @app.get("/programs/{program_id}")
    def program(program_id: str) -> dict[str, Any]:
        prog = get_program(store, program_id)
        if prog is None:
            raise HTTPException(status_code=404, detail=f"unknown program id: {program_id!r}")
        return prog

    @app.get("/atoms")
    def atoms(program: str | None = None, identity: str | None = None,
              tier: str | None = None) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if program is not None:
            digest = store.digest_for_program_id(program)
            if digest is None:
                return envelope([], program=program, identity=identity, tier=tier)
            filters["solution_digest"] = digest
        if identity is not None:
            filters["identity"] = identity
        if tier is not None:
            filters["tier"] = tier
        rows = []
        for atom in store.iter_atoms(**filters):
            d = atom.to_dict()
            d["program_id"] = program_id(d.pop("solution_digest"))
            rows.append(d)
        return envelope(rows, program=program, identity=identity, tier=tier)

    @app.post("/register")
    def register_solution(
        body: RegisterRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        token = _bearer_token(authorization)
        git = git_factory(token)
        reference = SolutionReference(**body.reference)
        evidence = Evidence.from_dict(body.evidence)
        try:
            result = register(
                store,
                auth,
                token=token,
                reference=reference,
                manifest=body.manifest,
                evidence=evidence,
                git=git,
                now=datetime.now(UTC).isoformat(),
            )
        except AuthError as e:
            raise HTTPException(status_code=401, detail=f"{type(e).__name__}: {e}") from e
        except WrongOwner as e:
            raise HTTPException(status_code=403, detail=f"{type(e).__name__}: {e}") from e
        except RegisterError as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e
        except GitHubReadError as e:
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
        return asdict(result)

    return app


def create_default_app() -> FastAPI:
    """The uvicorn entrypoint: ``uvicorn nethackers.hub.api:create_default_app
    --factory`` (see ``hub/Dockerfile``'s ``CMD``). A **factory**, not a
    module-level ``app`` -- called once at server startup, never at import
    time, so importing this module (as ``tests/hub/test_api.py`` and
    ``tests/hub/test_fixtures.py`` do, for ``create_app``) never opens a
    database as a side effect (task-14-context.md's Reconciliation 2).

    Configured entirely from the environment, for the container/dev-compose
    case (``compose.yaml``):

    - ``NETHACKERS_DB`` (default ``/data/hub.db``): the sqlite file path.
    - ``NETHACKERS_LOAD_FIXTURES=1``: seed the fresh store via
      ``nethackers.hub.fixtures.load_fixtures`` (dev/demo only -- imported
      lazily, only when this flag is actually set).
    - ``NETHACKERS_STUB_IDENTITIES``: a JSON ``{token: login}`` object --
      when set, auth is ``LocalStubAuth`` over that map (offline dev/demo).
      Otherwise auth is ``GitHubAppAuth(NETHACKERS_CLIENT_ID)`` (the real
      device-flow validator; ``NETHACKERS_CLIENT_ID`` is then required).
    """
    db = os.environ.get("NETHACKERS_DB", "/data/hub.db")
    store = Store(db)
    store.init_schema()
    if os.environ.get("NETHACKERS_LOAD_FIXTURES") == "1":
        from nethackers.hub.fixtures import load_fixtures

        load_fixtures(store)

    stub = os.environ.get("NETHACKERS_STUB_IDENTITIES")
    auth: AuthProvider
    if stub:
        auth = LocalStubAuth(json.loads(stub))
    else:
        auth = GitHubAppAuth(os.environ["NETHACKERS_CLIENT_ID"])
    return create_app(store, auth)
