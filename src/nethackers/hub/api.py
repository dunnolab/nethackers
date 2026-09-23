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
module's own listing/resolution endpoints -- ``GET /objectives`` and the
name->spec resolution ``GET /board``'s ``?scope=`` identity-kind branch
needs. ``GET /elites`` resolves its own ``?scope=`` purely via
``views.boards.resolve_scope`` (roles/facets/identities/``"generalist"``)
-- it never touches ``catalog`` at all, injected or module-level.
"""

from __future__ import annotations

import html as _html
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from nethackers.arena.seeds import secret_fingerprint as _fp
from nethackers.arena_version import ARENA_MAJOR, ARENA_MAJOR_BY_DIGEST
from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.hub.auth import AuthError, AuthProvider, GitHubAppAuth, LocalStubAuth
from nethackers.hub.envelope import envelope
from nethackers.hub.github import GitHubRead, GitHubReadError
from nethackers.hub.ids import program_id
from nethackers.hub.negotiate import prefers_markdown
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
from nethackers.hub.verify import (
    UnknownSolution,
    VerifierAuthError,
    VerifierConfig,
    VerifyError,
    record_attempt,
    register_verified,
    register_verified_baseline,
    resolve_verifier,
    verify_candidates,
)
from nethackers.hub.views.achievements import (
    coverage as achievements_coverage,
    firsts as achievements_firsts,
    milestones as achievements_milestones,
)
from nethackers.hub.views.baseline import read_baseline
from nethackers.hub.views.boards import aggregate_board, board, resolve_scope
from nethackers.hub.views.brief import render_brief
from nethackers.hub.views.elites import read_elites
from nethackers.hub.views.hackers import hacker_board, leaders as hackers_leaders
from nethackers.hub.views.programs import count_programs, get_program, list_programs
from nethackers.hub.views.progress import read_progress
from nethackers.hub.views.recognition import read_recognition
from nethackers.hub.views.solution import read_solution_frontier
from nethackers.hub.views.source import VERIFIED, Epoch, VerificationUnavailable, source_for
from nethackers.hub.views.stats import read_stats
from nethackers.hub.views.verified import read_verified, read_verified_baseline

# The index.html file shipped in the wheel package data.
_INDEX = Path(__file__).parent / "web" / "index.html"

# The link-preview card (1280x640), shipped in the wheel next to index.html and
# served at /social-preview.png; the page head points og:image at it.
_SOCIAL_PREVIEW = Path(__file__).parent / "web" / "social-preview.png"

# The canonical public origin, used to build the absolute og:url a share card
# needs. It is the same literal the page's own head already carries; keeping it
# here means a hacker link previews as itself rather than as the front page.
_SITE_URL = "https://nethackers.dunnolab.ai"


def _served_page() -> str:
    """The page as it goes out on the wire. Stamps the masthead {{version}}
    from the installed package at serve time, so it can never drift from
    pyproject the way a hardcoded string does. Read per-request (like
    ``_dict_audio_path``) -- cheap, and a pure function of the file + package
    metadata."""
    return _INDEX.read_text(encoding="utf-8").replace("{{version}}", _pkg_version("nethackers"))


def _retitle(page: str, title: str) -> str:
    """Rewrite the head's one ``<title>``."""
    return re.sub(r"(<title>)[^<]*(</title>)", lambda m: m[1] + title + m[2], page, count=1)


def _restamp(page: str, attr: str, value: str) -> str:
    """Rewrite the ``content`` of the one ``<meta {attr} ...>`` tag in the head.
    Keyed on the attribute (``property="og:title"``), never on the copy, so
    re-wording the card does not silently switch personalization off; a test
    pins the tags themselves."""
    return re.sub(
        rf'(<meta {re.escape(attr)} content=")[^"]*(">)',
        lambda m: m[1] + value + m[2], page, count=1,
    )


def _hacker_card(page: str, username: str) -> str:
    """Personalize the text-only link preview (Twitter/X, Telegram, Slack,
    Discord) of a ``/h/<username>`` page. ``username`` is the one piece of
    caller-controlled text in the head, so every stamped copy of it is
    HTML-escaped, and the og:url path segment is percent-encoded first."""
    who = _html.escape("@" + username, quote=True)
    desc = _html.escape(
        f"Registered programs and per-identity results for @{username} "
        "on the NetHackers frontier.", quote=True,
    )
    url = _html.escape(f"{_SITE_URL}/h/{quote(username, safe='')}", quote=True)
    page = _retitle(page, f"{who} &mdash; NetHackers")
    for attr, value in (
        ('name="description"', desc),
        ('property="og:title"', f"{who} on NetHackers"),
        ('property="og:description"', desc),
        ('property="og:url"', url),
        ('name="twitter:title"', f"{who} on NetHackers"),
        ('name="twitter:description"', desc),
    ):
        page = _restamp(page, attr, value)
    return page


def _dict_audio_path() -> Path:
    """The optional background track path, read from NETHACKERS_DICT_AUDIO env
    (default: the package web/dictionary.mp3). Read at request time so tests can
    monkeypatch without module reload."""
    return Path(os.environ.get(
        "NETHACKERS_DICT_AUDIO",
        str(Path(__file__).parent / "web" / "dictionary.mp3"),
    ))


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


class VerifyRequest(BaseModel):
    """The ``POST /verify`` envelope: a ``repo@commit`` reference (same raw
    ``{repo, commit}`` dict shape as ``RegisterRequest.reference``, parsed by
    hand into ``SolutionReference``) plus the verifier's own ``Evidence`` and
    the ``secret_fingerprint`` it computed over the hidden secret it was
    handed (``register_verified`` checks that fingerprint, never a raw
    secret, against the hub's own -- the raw hidden secret never appears in
    this request)."""

    reference: dict[str, str]
    evidence: dict[str, Any]
    secret_fingerprint: str


class VerifyBaselineRequest(BaseModel):
    """The ``POST /verify/baseline`` envelope: ``VerifyRequest`` minus the
    ``reference``. AutoAscend is the reference floor, not a participant, so
    there is no ``repo@commit`` to name -- the identity of the submission is
    fixed by the route itself, not supplied by the caller."""

    evidence: dict[str, Any]
    secret_fingerprint: str


class VerifyAttemptRequest(BaseModel):
    """The ``POST /verify/attempts`` envelope: a ``repo@commit`` reference,
    evaluator image digest, secret fingerprint, and attempt outcome
    (status, optional failure kind and message, identities completed)."""

    reference: dict[str, str]
    evaluator_image: str
    secret_fingerprint: str
    status: str
    failure_kind: str | None = None
    message: str | None = None
    identities_done: int = 0


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
    verifier: VerifierConfig | None = None,
) -> FastAPI:
    """Build a hub API app over ``store``/``auth``. Every route is a closure
    over ``store``/``auth``/``catalog``/``git_factory``/``verifier`` -- see
    module docstring for the catalog-injection scope. ``git_factory`` maps the
    caller's Bearer token to a commit-checker (default: a real ``GitHubRead``;
    tests inject a fake). Reads delegate straight to the view/store functions;
    nothing here executes candidate code.

    ``verifier`` is the optional ``VerifierConfig`` gating the verified-tier
    routes (``GET /verify/config``, ``POST /verify``; Tasks 6/7 add more) --
    defaults to ``None`` (verification unconfigured, routes 503) so every
    existing call site is unaffected. ``POST /verify`` writes into the
    isolated ``verified_atoms`` table via ``register_verified`` -- it never
    touches the self-reported ``atoms`` table ``POST /register`` owns."""
    app = FastAPI()

    def _epoch() -> Epoch | None:
        """The one verified epoch this hub can currently read, or None when no
        verifier is configured. ``ARENA_MAJOR`` is this hub's current arena
        major -- the comparability key every verified read is scoped to now,
        not any single pinned image digest. A verified atom reads back through
        this epoch as long as the digest it was written under classifies to
        this major in ``arena_version``; which exact digest that was is
        provenance, not part of the scope."""
        if verifier is None:
            return None
        return Epoch(
            secret_fingerprint=_fp(verifier.secret),
            arena_major=ARENA_MAJOR,
            seeds=verifier.seeds,
        )

    def _major_context(tier: str) -> int | None:
        """The arena major a response was read under, or None for a tier that
        is not scoped by one. ``envelope`` drops None, so a self-reported
        board gains no key."""
        epoch = _epoch()
        return epoch.arena_major if tier == VERIFIED and epoch else None

    def _source_guard(tier: str) -> None:
        """503 for a verified read on a hub with no verifier, matching the rest
        of /verify/*. Called before a view so the failure is an HTTP status,
        not a 500."""
        try:
            source_for(tier, _epoch())
        except VerificationUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # `auth` is additive (offline-identity/effective-identity feature): an
        # old client that only reads "status" is unaffected; a new client
        # reads it to show the hub's effective identity instead of a bare 401
        # on register with no hint why (see hubclient.client.HubClient.hub_mode).
        return {"status": "ok", "auth": auth.mode}

    def _brief() -> str:
        """The markdown representation, rendered from live store reads."""
        return render_brief(store, epoch=_epoch(), version=_pkg_version("nethackers"))

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def index(request: Request) -> Response:
        """Two representations of one resource, chosen by Accept (design
        2026-09-16). HTML is the default for everyone who did not explicitly
        ask for markdown, including `*/*` (I1).

        `Vary: Accept` goes on BOTH branches (I2). Caddy runs no response
        cache today, so the immediate exposure is the clients' own -- Claude
        Code holds a fetched URL for 15 minutes -- but this is the header that
        makes putting a CDN in front safe later.
        """
        if prefers_markdown(request.headers.get("accept")):
            return Response(content=_brief(),
                            media_type="text/markdown; charset=utf-8",
                            headers={"Vary": "Accept"})
        return Response(content=_served_page(),
                        media_type="text/html; charset=utf-8",
                        headers={"Vary": "Accept"})

    @app.get("/h/{username}", response_class=HTMLResponse)
    def hacker_page(username: str) -> str:
        """The deep link behind a hacker popup: the same single page as ``/``,
        which reads the handle back off the path and opens the popup itself.
        Deliberately no DB lookup -- an unregistered handle still gets the
        page, and the popup renders its own "no registered programs" state
        rather than a 404 that would cost a query on every page load.

        Not content-negotiated: it is a human deep link, and an agent that
        wants the data has /index.md and the JSON reads."""
        return _hacker_card(_served_page(), username)

    @app.api_route("/index.md", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/llms.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def brief_document() -> Response:
        """The same document at two conventional URLs (D9), as text/plain
        (D4). Stacked decorators register both paths against one handler.

        Honest expectation for /llms.txt: Ahrefs' May 2026 logs over 137,210
        domains found 97% of published llms.txt files were never fetched at
        all. It is here because Claude Code -- the client this design targets
        -- is the second-most-frequent fetcher of the ones that are read.
        """
        return Response(content=_brief(), media_type="text/plain; charset=utf-8")

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

    @app.get("/social-preview.png")
    def social_preview() -> FileResponse:
        """The og:image / twitter:image card. Package data, so unlike the
        background track it is never absent; a day of caching keeps the
        crawlers that unfurl a shared link from re-fetching it per share."""
        return FileResponse(
            _SOCIAL_PREVIEW, media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        # ``_epoch()`` is None on a hub with no verifier -- ``read_stats`` then
        # omits ``verified_programs`` entirely rather than reporting a 0 it
        # cannot stand behind.
        return read_stats(store, epoch=_epoch())

    @app.get("/baseline")
    def baseline(tier: str = "self-reported") -> dict[str, Any]:
        _source_guard(tier)
        return read_baseline(store, tier=tier, epoch=_epoch())

    @app.get("/recognition")
    def recognition(tier: str = "self-reported") -> dict[str, Any]:
        # Compound object -- {generated_at, keepers, breakthroughs} -- for the
        # website's Frontier Keepers and Greatest Breakthroughs tables.
        _source_guard(tier)
        return read_recognition(store, tier=tier, epoch=_epoch())

    @app.get("/progress")
    def progress(scope: str | None = None, tier: str = "self-reported") -> dict[str, Any]:
        return read_progress(store, scope=scope, tier=tier)

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
    def elites(scope: str = "generalist", tier: str = "self-reported") -> dict[str, Any]:
        _source_guard(tier)
        try:
            rows = read_elites(store, scope=scope, tier=tier, epoch=_epoch())
        except ValueError as e:
            raise HTTPException(status_code=404, detail=f"unknown scope: {scope!r}") from e
        return envelope(rows, scope=scope, tier=tier,
                        arena_major=_major_context(tier))

    @app.get("/board")
    def get_board(
        scope: str = "generalist",
        tier: str = "self-reported",
        metric: str | None = None,
    ) -> dict[str, Any]:
        # ?metric= (coverage/firsts) is retired -- those now live at
        # /achievements/coverage|/firsts (Part 1). Reject explicitly rather
        # than silently ignoring it and returning the (unrelated) ?scope=
        # board, which would mask a caller still on the old contract.
        if metric is not None:
            raise HTTPException(
                status_code=400,
                detail="?metric= is gone; use /achievements/coverage or /achievements/firsts",
            )
        _source_guard(tier)
        if scope in catalog and catalog[scope].kind == "identity":
            rows = board(store, catalog[scope], tier=tier, epoch=_epoch())
        else:
            try:
                _kind, ids = resolve_scope(scope)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=f"unknown scope: {scope!r}") from e
            rows = aggregate_board(store, ids, tier=tier, epoch=_epoch())
        return envelope(rows, scope=scope, tier=tier,
                        arena_major=_major_context(tier))

    @app.get("/hackers")
    def hackers(scope: str = "generalist", tier: str = "self-reported") -> dict[str, Any]:
        try:
            _kind, ids = resolve_scope(scope)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=f"unknown scope: {scope!r}") from e
        return envelope(hacker_board(store, ids, tier=tier), scope=scope, tier=tier)

    @app.get("/hackers/random")
    def hackers_random(n: int = 20) -> dict[str, Any]:
        """Up to ``n`` (clamped to 20) random registered hacker handles,
        sampled server-side -- names for the dungeon-wall @username runners.
        Cheaper than /hackers: no coverage/mean aggregation."""
        return envelope(store.random_owners(max(0, min(20, n))), n=n)

    @app.get("/hackers/leaders")
    def hackers_leaders_route(by: str, tier: str = "self-reported") -> dict[str, Any]:
        try:
            rows = hackers_leaders(store, by, tier=tier)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return envelope(rows, by=by, tier=tier)

    @app.get("/programs")
    def programs(owner: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return envelope(list_programs(store, owner=owner, limit=limit, offset=offset),
                        owner=owner, total=count_programs(store, owner=owner))

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

    # TODO(launch): /atoms is an unbounded full-table read -- paginate before
    # public exposure (see hub security audit).
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

    @app.get("/verify/config")
    def verify_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if verifier is None:
            raise HTTPException(status_code=503, detail="verification not configured")
        token = _bearer_token(authorization)
        try:
            resolve_verifier(token, verifier)
        except VerifierAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        return {"secret": verifier.secret, "seeds": list(verifier.seeds)}

    @app.post("/verify")
    def verify_solution(
        body: VerifyRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if verifier is None:
            raise HTTPException(status_code=503, detail="verification not configured")
        token = _bearer_token(authorization)
        try:
            tok_fp = resolve_verifier(token, verifier)
        except VerifierAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        try:
            result = register_verified(
                store,
                reference=SolutionReference(**body.reference),
                evidence=Evidence.from_dict(body.evidence),
                secret_fingerprint=body.secret_fingerprint,
                verifier_token_fingerprint=tok_fp,
                now=datetime.now(UTC).isoformat(),
                current_major=ARENA_MAJOR,
                hub_secret=verifier.secret,
                seeds=verifier.seeds,
            )
        except UnknownSolution as e:
            raise HTTPException(status_code=404, detail=f"{type(e).__name__}: {e}") from e
        except VerifyError as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e
        return {
            "inserted": result.inserted,
            "ignored": result.total - result.done,
            "coverage": {"done": result.done, "total": result.total},
        }

    @app.post("/verify/baseline")
    def verify_baseline(
        body: VerifyBaselineRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        # AutoAscend's hidden-seed floor. Token-gated exactly like POST
        # /verify -- an unauthenticated writer able to lower the floor would
        # inflate every program's Delta-vs-AA just as surely as one able to
        # raise a program's own score.
        if verifier is None:
            raise HTTPException(status_code=503, detail="verification not configured")
        token = _bearer_token(authorization)
        try:
            tok_fp = resolve_verifier(token, verifier)
        except VerifierAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        try:
            result = register_verified_baseline(
                store,
                evidence=Evidence.from_dict(body.evidence),
                secret_fingerprint=body.secret_fingerprint,
                verifier_token_fingerprint=tok_fp,
                current_major=ARENA_MAJOR,
                hub_secret=verifier.secret,
                seeds=verifier.seeds,
            )
        except VerifyError as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e
        return {
            "inserted": result.inserted,
            "coverage": {"done": result.done, "total": result.total},
        }

    @app.post("/verify/attempts")
    def verify_attempt(
        body: VerifyAttemptRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if verifier is None:
            raise HTTPException(status_code=503, detail="verification not configured")
        token = _bearer_token(authorization)
        try:
            tok_fp = resolve_verifier(token, verifier)
        except VerifierAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        try:
            record_attempt(
                store, reference=SolutionReference(**body.reference),
                secret_fingerprint=body.secret_fingerprint,
                evaluator_image=body.evaluator_image,
                verifier_token_fingerprint=tok_fp, status=body.status,
                failure_kind=body.failure_kind,
                message=body.message, identities_done=body.identities_done,
                now=datetime.now(UTC).isoformat())
        except VerifyError as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e
        return {"ok": True}

    @app.get("/verify/candidates")
    def verify_candidates_route(
        limit: int = 8, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if verifier is None:
            raise HTTPException(status_code=503, detail="verification not configured")
        token = _bearer_token(authorization)
        try:
            resolve_verifier(token, verifier)
        except VerifierAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        rows = verify_candidates(
            store, secret_fingerprint=_fp(verifier.secret),
            arena_major=ARENA_MAJOR, seeds=verifier.seeds, limit=limit,
        )
        return envelope(rows)

    @app.get("/verify/overview")
    def verify_overview() -> dict[str, Any]:
        # Public: per-identity aggregates only, never raw per-seed rows or
        # seed ids (the seeds are secret). ``baseline`` is AutoAscend's floor
        # on the same hidden seeds under the same epoch -- what makes a
        # verified progression readable as "vs AutoAscend" rather than a bare
        # number. ``arena_major``/``arena_digests`` are additive too: callers
        # reading only per_identity/overall/baseline are unaffected. They
        # name which digests the current major accepts, so a reader can tell
        # a rebuild-driven gap in the corpus from a real regression without
        # cross-referencing ``arena_version`` by hand.
        empty: dict[str, Any] = {"per_identity": {}, "overall": None}
        majors = {
            "arena_major": ARENA_MAJOR,
            "arena_digests": sorted(
                d for d, m in ARENA_MAJOR_BY_DIGEST.items() if m == ARENA_MAJOR
            ),
        }
        if verifier is None:
            # No verifier configured to read through, but the major is a fact
            # about this build of the code, not about any live data -- true
            # to report even when there is nothing else to report.
            return {**empty, "baseline": empty, **majors}
        # Explicit kwargs rather than a **dict: the two reads MUST share one
        # epoch scope (a Delta across epochs is meaningless), and spelling it
        # out keeps that checkable by the typechecker.
        fingerprint, seeds = _fp(verifier.secret), verifier.seeds
        return {
            **read_verified(store, secret_fingerprint=fingerprint, seeds=seeds,
                            arena_major=ARENA_MAJOR),
            "baseline": read_verified_baseline(store, secret_fingerprint=fingerprint,
                                               seeds=seeds, arena_major=ARENA_MAJOR),
            **majors,
        }

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
    - ``NETHACKERS_HIDDEN_SECRET``: the verified-tier hidden-eval secret. When
      unset, ``verifier`` stays ``None`` and the verified-tier routes 503.
    - ``NETHACKERS_HIDDEN_SEEDS`` (default ``"[]"``): a JSON list of the
      hidden seed ints, read only when ``NETHACKERS_HIDDEN_SECRET`` is set.
    - ``NETHACKERS_VERIFIER_TOKENS``: a comma-separated list of tokens
      accepted as verifier auth (empties dropped), read only when
      ``NETHACKERS_HIDDEN_SECRET`` is set.
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

    verifier: VerifierConfig | None = None
    secret = os.environ.get("NETHACKERS_HIDDEN_SECRET")
    if secret:
        seeds = tuple(json.loads(os.environ.get("NETHACKERS_HIDDEN_SEEDS", "[]")))
        raw_tokens = os.environ.get("NETHACKERS_VERIFIER_TOKENS", "").split(",")
        tokens = frozenset(t for t in raw_tokens if t)
        verifier = VerifierConfig(tokens=tokens, secret=secret, seeds=seeds)
    app = create_app(store, auth, verifier=verifier)
    _limit_worker_threads(app, int(os.environ.get("NETHACKERS_THREADS", "2")))
    return app


def _limit_worker_threads(app: FastAPI, total: int) -> None:
    """Cap the threadpool FastAPI runs this package's sync handlers in.

    Every handler here is a sync ``def``, so each request occupies a worker
    thread doing almost pure Python (rows -> objects -> JSON). AnyIO's
    default of 40 lets 40 such threads fight over the GIL, and measured
    throughput *falls* as load rises -- 7.1 page views/s at one client down
    to 0.4/s at ten, a convoy, not saturation. Capping the pool keeps
    throughput flat instead: at 40 concurrent clients, 2 threads served
    ~16x more than 40 did.

    Set via ``NETHACKERS_THREADS`` (default 2). Raise it only alongside
    evidence -- more threads is what causes the collapse, not what cures
    it. Applied per worker process, so N uvicorn workers give N*total."""
    @app.on_event("startup")
    async def _cap() -> None:  # pragma: no cover - startup hook
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = total
