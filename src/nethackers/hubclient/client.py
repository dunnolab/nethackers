"""Hub HTTP client (M2a Task 13): a thin wrapper over ``httpx`` matching
Task 12's API surface (``nethackers.hub.api``) endpoint for endpoint -- one
method per read route plus ``register``, each building exactly the
URL/params/headers/body the server expects. ``http`` defaults to the real
``httpx`` module but is injectable, so ``tests/test_hubclient.py`` drives
every method against a fake recording calls -- no real network access
anywhere in this module.

``render_elites``/``render_board``/``render_search``/``render_show``/
``plain_frontier`` are pure formatters over the JSON a read call returns --
they don't touch ``HubClient`` at all. The first four were the CLI's only
renderers pre-CLI-UX-pass; now they're the
**``plain``** half of ``nethackers.hubclient.output.emit``'s
``table=``/``plain=`` pair (the ``rich`` half lives in
``nethackers.hubclient.render``) -- reached via ``-o plain``, and still
exactly what the CLI's raw-JSON modes never touch (``emit`` prints
``json.dumps`` of the untouched response for ``-o json``, same as this
module's callers always could). Built on three shared helpers
(final-review fix, folding in a CLI-UX pass): ``_table`` (an aligned ASCII
table -- per-column widths, left-aligned text / right-aligned numeric
columns, a header + rule, 2-space gutters), ``_short_digest`` (the first
~12 characters *after* stripping a leading ``"sha256:"``, so distinct
digests stay visually distinguishable in a column instead of collapsing
onto the shared prefix), and ``_num`` (floats rounded to 3 decimals, ints
passed through) -- ``nethackers.hubclient.render``'s ``rich`` renderers
reuse ``_short_digest``/``_num`` too, rather than duplicating them. Pure
Python, no ``rich`` dependency here -- see the M2a fix reports for why this
half stays plain. Every renderer prints a friendly one-line message
instead of a bare header for an empty response -- never a crash, never a
table of nothing.

``plain_frontier`` is ``render_frontier_grid``'s plain-text counterpart
(same ``{identity: value}`` input), but unlike it, ``plain_frontier``
DOES collapse to the shared friendly-one-liner convention above -- it
lists only the identities present in ``scores`` rather than padding out
the full 73-``IDENTITIES`` universe, so it has no fixed shape to fall back
on the way the rich grid does.
"""

from __future__ import annotations

from typing import Any

import httpx

from nethackers.hubclient.auth import AuthError, TokenSource

# The register-401 hint shown whenever the pointed-at hub isn't confirmed to
# be a real github-backed one (a confirmed offline/stub hub, OR an old hub
# that doesn't report a mode at all -- the common local-dev case): the
# motivating bug was a real GitHub login getting a bare 401 from a local
# stub hub with no clue why.
_OFFLINE_401_HINT = (
    "hub rejected your token — it's an OFFLINE (stub) hub and only accepts "
    "the built-in offline identity; run the hub with HUB_AUTH=github for "
    "real registration."
)


class HubUnreachable(Exception):
    """Raised by ``HubClient.hub_mode`` when the hub can't be reached at all
    (connection refused, timeout, DNS failure, ...) -- never a raw httpx
    transport error. Distinguishes "can't reach the hub" from a reachable
    hub whose response simply has no ``auth`` field (``hub_mode`` returns
    ``None`` for that -- an older hub, from before this feature)."""


class HubClient:
    """Thin HTTP wrapper over the hub API. Every method mirrors one
    endpoint's exact request shape; ``http`` is injectable (default: the
    real ``httpx`` module) so callers -- including tests -- can swap in a
    fake transport.

    ``token_source``, when given, makes ``register`` self-healing: it
    resolves the Bearer token via ``token_source.current()`` (proactive
    refresh already happened there if needed) and, on an actual 401 from the
    hub, reactively calls ``token_source.refresh()`` and retries the request
    exactly once. A 401 that survives the retry becomes an ``AuthError`` --
    never a bare ``httpx.HTTPStatusError`` a caller has to know to interpret.
    With no ``token_source`` (the default), ``register`` keeps today's
    behavior exactly: send the passed ``token=`` string, no retry."""

    def __init__(self, base_url: str, *, http: Any = httpx,
                 token_source: TokenSource | None = None,
                 timeout: float | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._token_source = token_source
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # ``timeout`` is only forwarded when set, so callers that inject a fake
        # ``http`` whose ``get`` has no ``timeout`` kwarg (the tests) are
        # untouched. The TUI passes one so a slow hub fails fast on its worker
        # thread instead of leaving the loading animation up indefinitely.
        kwargs: dict[str, Any] = {"params": params}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        response = self._http.get(self._base + path, **kwargs)
        response.raise_for_status()
        return response.json()

    def objectives(self) -> Any:
        """``GET /objectives`` -- the catalog listing."""
        return self._get("/objectives")

    def baseline(self) -> Any:
        """``GET /baseline`` -- AutoAscend's per-identity reference floor."""
        return self._get("/baseline")

    def elites(self, scope: str = "generalist", tier: str = "self-reported") -> Any:
        """``GET /elites?scope=`` -- the ranked rows (envelope unwrapped)."""
        return (self._get("/elites", {"scope": scope, "tier": tier}) or {}).get("rows", [])

    def board(self, scope: str = "generalist", tier: str = "self-reported") -> Any:
        """``GET /board?scope=`` -- the ranked rows (envelope unwrapped)."""
        return (self._get("/board", {"scope": scope, "tier": tier}) or {}).get("rows", [])

    def search(self, owner: str | None = None, limit: int = 50, offset: int = 0) -> Any:
        """``GET /programs``, with ``?owner=`` only when given and
        ``?limit=``/``?offset=`` always (they default server-side too, but
        are sent explicitly here). Envelope unwrapped -- returns the rows."""
        params = {
            k: v
            for k, v in (("owner", owner), ("limit", limit), ("offset", offset))
            if v is not None
        }
        return (self._get("/programs", params) or {}).get("rows", [])

    def show(self, program_id: str) -> Any:
        """``GET /programs/{id}`` -- the ``{id, owner, reference:{repo,
        commit}, registered_at}`` object (a single resource, not enveloped)."""
        return self._get(f"/programs/{program_id}")

    def program_identities(self, program_id: str) -> Any:
        """``GET /programs/{id}/identities`` -- that program's per-identity
        mean progression rows (envelope unwrapped). Takes an opaque
        ``program_id``, never a raw solution digest -- the retired
        ``solution_frontier`` method this replaced only ever understood the
        latter."""
        return (self._get(f"/programs/{program_id}/identities") or {}).get("rows", [])

    def hub_mode(self) -> str | None:
        """``GET /healthz`` (unauthenticated) and return the pointed-at hub's
        reported ``auth`` mode (``"offline"``/``"github"``), or ``None`` when
        the hub is reachable but its response has no ``auth`` field -- an
        older hub, from before this feature (a purely additive field: no
        hub upgrade is forced). Raises ``HubUnreachable`` -- never a raw
        httpx transport error -- when the hub can't be reached at all, so a
        caller can tell "unreachable" apart from "reachable, mode unknown"."""
        kwargs: dict[str, Any] = {}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        try:
            response = self._http.get(self._base + "/healthz", **kwargs)
            response.raise_for_status()
            mode = response.json().get("auth")
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers a non-JSON 200 (JSONDecodeError): treat a
            # reachable-but-unparseable hub as unreachable, never a raw crash.
            raise HubUnreachable(self._base) from exc
        return mode if isinstance(mode, str) else None

    def _auth_error_for_401(self, fallback: str) -> AuthError:
        """Build the ``AuthError`` for a register 401: the OFFLINE-hub hint
        (``_OFFLINE_401_HINT``) when the pointed-at hub isn't confirmed
        ``"github"`` mode -- covers both a confirmed offline/stub hub and an
        old hub that reports no mode at all (``hub_mode`` raising
        ``HubUnreachable`` at this diagnostic step counts as "not confirmed"
        too, best-effort) -- else ``fallback``, unchanged from before this
        hint existed."""
        try:
            mode = self.hub_mode()
        except HubUnreachable:
            mode = None
        return AuthError(_OFFLINE_401_HINT) if mode != "github" else AuthError(fallback)

    def _post_register(self, token: str, reference: dict[str, Any],
                       manifest: dict[str, Any], evidence: dict[str, Any]) -> Any:
        kwargs: dict[str, Any] = {
            "json": {"reference": reference, "manifest": manifest, "evidence": evidence},
            "headers": {"Authorization": f"Bearer {token}"},
        }
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        response = self._http.post(self._base + "/register", **kwargs)
        response.raise_for_status()
        return response.json()

    def register(
        self,
        *,
        token: str | None = None,
        reference: dict[str, Any],
        manifest: dict[str, Any],
        evidence: dict[str, Any],
    ) -> Any:
        """``POST /register`` with a ``Bearer`` token and the
        ``{reference, manifest, evidence}`` body the hub expects: the
        ``repo@commit`` link (``reference``, the solution identity), the
        solution ``manifest``, and the self-reported ``evidence`` (an
        ``Evidence.to_dict()``). This method only transports them -- the hub
        validates the link and the batch and writes per-identity atoms.

        ``token`` is used as-is only when this client has no
        ``token_source`` -- see the class docstring for the self-healing
        401 -> refresh -> retry path taken when one is set (``token`` is
        then ignored).

        A 401 always surfaces as a clear ``AuthError`` -- never a bare
        ``httpx.HTTPStatusError`` with no hint why (the motivating bug: a
        real GitHub login pointed at a local offline/stub hub, which only
        ever knows its one built-in identity) -- whether or not a
        ``token_source`` is set; see ``_auth_error_for_401``."""
        if self._token_source is None:
            if token is None:
                raise ValueError(
                    "HubClient.register requires token= when no token_source is set")
            try:
                return self._post_register(token, reference, manifest, evidence)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 401:
                    raise
                raise self._auth_error_for_401("hub rejected your token (401) — run "
                                               "`nethackers login` to refresh it") from exc
        tok = self._token_source.current()
        try:
            return self._post_register(tok, reference, manifest, evidence)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            tok = self._token_source.refresh()
            try:
                return self._post_register(tok, reference, manifest, evidence)
            except httpx.HTTPStatusError as exc2:
                if exc2.response.status_code == 401:
                    raise self._auth_error_for_401(
                        "the hub rejected the token even after refresh") from exc2
                raise


def _is_numeric(cell: str) -> bool:
    """Whether ``cell`` parses as a number -- ``_table``'s per-column
    right-align test. Empty cells don't count as numeric (a column of all-
    blank cells should stay left-aligned, not right-align nothing)."""
    if cell == "":
        return False
    try:
        float(cell)
    except ValueError:
        return False
    return True


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """An aligned ASCII table: per-column widths sized to the widest of a
    column's header/cells, text columns left-aligned, columns where every
    row's cell parses as a number right-aligned, a header row, a ``-``-rule
    underneath it, and 2-space gutters between columns.

    Always at least the header + rule, even for ``rows == []``; never
    raises on a short row (a missing trailing cell renders blank)."""
    ncols = len(headers)
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows if i < len(r)), default=0))
        for i in range(ncols)
    ]
    numeric = [
        bool(rows) and all(_is_numeric(r[i]) for r in rows if i < len(r)) for i in range(ncols)
    ]

    def _row(cells: list[str]) -> str:
        padded = []
        for i in range(ncols):
            cell = cells[i] if i < len(cells) else ""
            padded.append(cell.rjust(widths[i]) if numeric[i] else cell.ljust(widths[i]))
        return "  ".join(padded).rstrip()

    lines = [_row(headers), _row(["-" * w for w in widths])]
    lines.extend(_row(r) for r in rows)
    return "\n".join(lines)


def _short_digest(digest: str, n: int = 12) -> str:
    """Shorten a (possibly ``"sha256:"``-prefixed) digest/hash to its first
    ``n`` characters *after* stripping that prefix, so distinct digests
    stay visually distinguishable in a table column. (Slicing the raw
    ``"sha256:..."`` string instead collapses every digest down to the
    shared 7-char prefix plus a handful of real characters -- the bug this
    fixes.)"""
    s = str(digest)
    if s.startswith("sha256:"):
        s = s[len("sha256:") :]
    return s[:n]


def _num(x: Any, nd: int = 3) -> str:
    """Render a metric for a table cell: floats rounded to ``nd`` decimals
    (``f"{x:.{nd}f}"``, so ``0.5087697678994835`` -> ``"0.509"``); ints,
    bools, ``None``, and anything else pass through as plain ``str``."""
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def plain_frontier(scores: dict[str, float | None]) -> str:
    """A pure-text rendering of a frontier ``{identity: value}`` map -- the
    same shape ``nethackers.hubclient.render``'s ``render_frontier_grid``
    draws as a role grid, here as one ``f"{identity:<20} {value:.2f}"``
    line per identity actually present in ``scores`` (a ``None`` value
    renders ``"—"`` instead of a number), sorted so identities group by
    role (a role code is an identity's leading segment, and sorting
    identity strings sorts by role first), with a blank line between role
    groups. A friendly one-line message -- not an empty string, not a bare
    ``"—"`` -- when ``scores`` is empty or every value in it is ``None``."""
    if not scores or all(v is None for v in scores.values()):
        return "no frontier data yet."

    lines: list[str] = []
    prev_role: str | None = None
    for identity in sorted(scores):
        role = identity.split("-")[0]
        if prev_role is not None and role != prev_role:
            lines.append("")
        prev_role = role
        value = scores[identity]
        rendered = f"{value:.2f}" if value is not None else "—"
        lines.append(f"{identity:<20} {rendered}")
    return "\n".join(lines)


def render_elites(entries: list[dict[str, Any]]) -> str:
    """A table of elite entries: ``rank | identity | program | score``
    (``program`` the opaque ``program_id``, shown verbatim -- already short,
    never truncated; ``score`` rounded via ``_num``). A friendly one-line
    message instead of a bare header when ``entries == []``."""
    if not entries:
        return "no elites recorded yet."
    headers = ["rank", "identity", "program", "score"]
    rows = [
        [
            str(entry.get("rank", "")),
            str(entry.get("identity", "")),
            str(entry.get("program_id", "")),
            _num(entry.get("score", "")),
        ]
        for entry in entries
    ]
    return _table(headers, rows)


def render_board(entries: list[dict[str, Any]]) -> str:
    """A table of board entries, shape-aware over which metric produced
    them (``program`` is the opaque ``program_id`` -- already short,
    verbatim, never truncated):

    - grading board (``/board``'s one row shape -- entries carry
      ``ascensions``): ``rank | program | owner | asc | median | mean``
      (``median``/``mean`` via ``_num``).
    - coverage board (``/achievements/coverage`` -- entries carry
      ``cells_held``): ``rank | program | owner | cells``.
    - firsts board (``/achievements/firsts`` -- entries carry ``firsts``):
      ``rank | program | owner | firsts``.

    A friendly one-line message instead of a bare header when
    ``entries == []`` (there's no shape to detect from zero rows anyway --
    this also subsumes the old blank-column papercut, since a shape is
    now always resolved from real entries, never guessed)."""
    if not entries:
        return "no board entries yet."

    first = entries[0]
    if "ascensions" in first:
        headers = ["rank", "program", "owner", "asc", "median", "mean"]
        rows = [
            [
                str(e.get("rank", "")),
                str(e.get("program_id", "")),
                str(e.get("owner", "")),
                str(e.get("ascensions", "")),
                _num(e.get("median_progression", "")),
                _num(e.get("mean_progression", "")),
            ]
            for e in entries
        ]
    elif "cells_held" in first:
        headers = ["rank", "program", "owner", "cells"]
        rows = [
            [
                str(e.get("rank", "")),
                str(e.get("program_id", "")),
                str(e.get("owner", "")),
                str(e.get("cells_held", "")),
            ]
            for e in entries
        ]
    elif "firsts" in first:
        headers = ["rank", "program", "owner", "firsts"]
        rows = [
            [
                str(e.get("rank", "")),
                str(e.get("program_id", "")),
                str(e.get("owner", "")),
                str(e.get("firsts", "")),
            ]
            for e in entries
        ]
    else:
        # Unknown/future board shape: fall back to whatever keys the first
        # entry actually has rather than guessing -- still never a crash.
        headers = sorted(first)
        rows = [[str(e.get(h, "")) for h in headers] for e in entries]
    return _table(headers, rows)


def render_search(results: list[dict[str, Any]]) -> str:
    """A table of registered programs: ``program | owner | repo | commit |
    registered`` (``program`` the opaque ``id`` shown verbatim -- already
    short, never truncated; ``commit`` shortened via ``_short_digest``, a
    real git object unlike the retired content-hash digest). A friendly
    one-line message instead of a bare header when ``results == []``."""
    if not results:
        return "no programs found."
    headers = ["program", "owner", "repo", "commit", "registered"]
    rows = [
        [
            str(r.get("id", "")),
            str(r.get("owner", "")),
            str((r.get("reference") or {}).get("repo", "")),
            _short_digest(str((r.get("reference") or {}).get("commit", ""))),
            str(r.get("registered_at", "")),
        ]
        for r in results
    ]
    return _table(headers, rows)


def render_show(program: dict[str, Any]) -> str:
    """An aligned ``key: value`` block describing one registered program
    (the ``/programs/{id}`` object: ``{id, owner, reference:{repo,commit},
    registered_at}``) -- ``id`` shown verbatim (already short, opaque),
    ``reference`` flattened into ``repo``/``commit`` (``commit`` shortened
    via ``_short_digest``, a real git object unlike the retired digest). A
    friendly one-line message instead of an empty block when ``program`` is
    empty/missing."""
    if not program:
        return "no such program."
    reference = program.get("reference") or {}
    flat = {
        "id": program.get("id", ""),
        "repo": reference.get("repo", ""),
        "commit": reference.get("commit", ""),
        "owner": program.get("owner", ""),
        "registered_at": program.get("registered_at", ""),
    }
    keys = ["id", "repo", "commit", "owner", "registered_at"]
    width = max(len(k) for k in keys)
    lines = []
    for key in keys:
        value = flat[key]
        if key == "commit":
            value = _short_digest(str(value))
        lines.append(f"{key:<{width}}: {value}")
    return "\n".join(lines)
