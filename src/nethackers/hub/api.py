"""Hub FastAPI app (M2a Task 12): the thin HTTP surface over the read views
and the register write-path built in Tasks 5-11. See task-12-context.md,
which governs this implementation. Nothing in this module runs candidate
code, computes a score, or does anything ``store``/``auth``/the register
ladder don't already do -- every handler just parses its request, delegates,
and maps the result/exception to an HTTP response.

``create_app(store, auth, *, catalog=CATALOG)`` builds a fresh ``FastAPI``
app with every handler a closure over its own ``store``/``auth``/``catalog``
-- no module-level app singleton, so each caller (each test) gets an
isolated instance. Reads hit the view/store functions directly.
``POST /register`` extracts a Bearer token, wraps the request body's
``manifest`` + ``evidence.solution_digest`` in a ``LocalStubGit`` (M2a's
self-reported trust model -- see validate.py's module docstring: real
subprocess-git verification is M2b-parked), and delegates to
``validate.register``, mapping its exceptions to HTTP status codes.

**Catalog-injection scope (M2a):** the injected ``catalog`` drives only this
module's own listing/resolution endpoints -- ``GET /objectives``,
``GET /objectives/{name}/batch``, and the name->spec resolution
``GET /board``'s ``?objective=`` branch needs. ``GET /elites`` and
``POST /register`` delegate straight to ``views.elites.read_elites`` /
``validate.register``, which both resolve against the *module*
``nethackers.hub.objectives.CATALOG`` -- not whatever ``catalog`` this app
was built with. In normal use (the default ``catalog=CATALOG``) every
endpoint agrees, since both are the same dict object. Threading a genuinely
*divergent* catalog all the way through to ``read_elites``/``register`` as
well would mean changing their own signatures (Tasks 8/11) -- parked, out of
this task's scope.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.hub.auth import AuthError, AuthProvider
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import (
    LocalStubGit,
    RegisterError,
    SolutionReference,
    WrongOwner,
    register,
)
from nethackers.hub.views.attainment import read_attainment
from nethackers.hub.views.boards import board, coverage_board, firsts_board
from nethackers.hub.views.elites import read_elites

# GET /search's column list, local to this module -- the context calls for
# keeping this one small SELECT in api.py rather than adding a store.py
# method for it.
_SEARCH_COLUMNS: tuple[str, ...] = (
    "digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at",
)


class RegisterRequest(BaseModel):
    """The ``POST /register`` envelope. ``reference``/``manifest``/
    ``evidence`` are kept as raw dicts -- the handler parses them by hand
    (``SolutionReference(**...)``, ``Evidence.from_dict(...)``) rather than
    mirroring their shape as nested Pydantic models, since the dataclasses
    in ``validate.py``/``contracts.models`` are already the source of truth
    for it."""

    reference: dict[str, str]
    manifest: dict[str, Any]
    evidence: dict[str, Any]


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
    store: Store, auth: AuthProvider, *, catalog: dict[str, ObjectiveSpec] = CATALOG
) -> FastAPI:
    """Build a hub API app over ``store``/``auth``. Every route is a closure
    over ``store``/``auth``/``catalog`` -- see module docstring for the
    catalog-injection scope. Reads delegate straight to the view/store
    functions; nothing here executes candidate code."""
    app = FastAPI()

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
            spec = catalog.get(objective)
            if spec is None:
                raise HTTPException(status_code=404, detail=f"unknown objective: {objective!r}")
            return board(store, spec, tier=tier)
        if metric == "coverage":
            return coverage_board(store)
        if metric == "firsts":
            return firsts_board(store)
        raise HTTPException(status_code=400, detail=f"unknown metric: {metric!r}")

    @app.get("/solutions/{digest}")
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

    @app.post("/register")
    def register_solution(
        body: RegisterRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        token = _bearer_token(authorization)
        evidence = Evidence.from_dict(body.evidence)
        reference = SolutionReference(**body.reference)
        # M2a trust: the git seam is a stub built straight from the
        # request's own manifest + claimed digest, so commit_exists/
        # fetch_manifest/content_digest all pass -- see module docstring.
        git = LocalStubGit(manifest=body.manifest, digest=evidence.solution_digest, exists=True)
        now = datetime.now(UTC).isoformat()
        try:
            result = register(
                store, auth, token=token, reference=reference, evidence=evidence, git=git, now=now
            )
        except AuthError as e:
            raise HTTPException(status_code=401, detail=f"{type(e).__name__}: {e}") from e
        except WrongOwner as e:
            raise HTTPException(status_code=403, detail=f"{type(e).__name__}: {e}") from e
        except RegisterError as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e
        return asdict(result)

    return app
