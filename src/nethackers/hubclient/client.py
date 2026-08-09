"""Hub HTTP client (M2a Task 13): a thin wrapper over ``httpx`` matching
Task 12's API surface (``nethackers.hub.api``) endpoint for endpoint -- one
method per read route plus ``register``, each building exactly the
URL/params/headers/body the server expects. ``http`` defaults to the real
``httpx`` module but is injectable, so ``tests/test_hubclient.py`` drives
every method against a fake recording calls -- no real network access
anywhere in this module.

``render_attainment``/``render_elites``/``render_board``/``render_search``/
``render_show`` are pure formatters over the JSON a read call returns --
they don't touch ``HubClient`` at all. They were the CLI's only renderers
pre-CLI-UX-pass; now they're the **``plain``** half of
``nethackers.hubclient.output.emit``'s ``table=``/``plain=`` pair (the
``rich`` half lives in ``nethackers.hubclient.render``) -- reached via
``-o plain``, and still exactly what the CLI's raw-JSON modes never touch
(``emit`` prints ``json.dumps`` of the untouched response for ``-o json``,
same as this module's callers always could). Built on three shared helpers
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
"""

from __future__ import annotations

from typing import Any

import httpx


class HubClient:
    """Thin HTTP wrapper over the hub API. Every method mirrors one
    endpoint's exact request shape; ``http`` is injectable (default: the
    real ``httpx`` module) so callers -- including tests -- can swap in a
    fake transport."""

    def __init__(self, base_url: str, *, http: Any = httpx) -> None:
        self._base = base_url.rstrip("/")
        self._http = http

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._http.get(self._base + path, params=params)
        response.raise_for_status()
        return response.json()

    def objectives(self) -> Any:
        """``GET /objectives`` -- the catalog listing."""
        return self._get("/objectives")

    def objective_batch(self, name: str) -> Any:
        """``GET /objectives/{name}/batch`` -- that objective's published
        ``(seed, character)`` pairs."""
        return self._get(f"/objectives/{name}/batch")

    def attainment(self, identity: str | None = None) -> Any:
        """``GET /attainment``, optionally narrowed to one ``?identity=``."""
        return self._get("/attainment", {"identity": identity} if identity else None)

    def elites(self, objective: str) -> Any:
        """``GET /elites?objective=...``."""
        return self._get("/elites", {"objective": objective})

    def board(self, objective: str | None = None, metric: str | None = None) -> Any:
        """``GET /board``, with whichever of ``?objective=``/``?metric=``
        was given (the server requires exactly one; this method just passes
        through whatever the caller supplied)."""
        params = {k: v for k, v in (("objective", objective), ("metric", metric)) if v}
        return self._get("/board", params)

    def search(self, owner: str | None = None, limit: int = 50, offset: int = 0) -> Any:
        """``GET /search``, with ``?owner=`` only when given and
        ``?limit=``/``?offset=`` always (they default server-side too, but
        are sent explicitly here)."""
        params = {
            k: v
            for k, v in (("owner", owner), ("limit", limit), ("offset", offset))
            if v is not None
        }
        return self._get("/search", params)

    def show(self, digest: str) -> Any:
        """``GET /solutions/{digest}``."""
        return self._get(f"/solutions/{digest}")

    def register(
        self,
        *,
        token: str,
        reference: dict[str, Any],
        manifest: dict[str, Any],
        evidence: dict[str, Any],
    ) -> Any:
        """``POST /register`` with a ``Bearer`` token and the
        ``{reference, manifest, evidence}`` body Task 12's
        ``RegisterRequest`` expects. ``reference``/``manifest``/``evidence``
        are plain dicts (``evidence`` is an ``Evidence.to_dict()``) -- this
        method doesn't parse or validate their shape, it only transports
        them."""
        response = self._http.post(
            self._base + "/register",
            json={"reference": reference, "manifest": manifest, "evidence": evidence},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()


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


def render_attainment(cells: list[dict[str, Any]]) -> str:
    """A table of attainment cells: ``identity | milestone | first_owner |
    holders`` (``holders`` <- ``cell["holder_count"]``). A friendly
    one-line message instead of a bare header when ``cells == []``."""
    if not cells:
        return "no attainment cells yet."
    headers = ["identity", "milestone", "first_owner", "holders"]
    rows = [
        [
            str(cell.get("identity", "")),
            str(cell.get("milestone", "")),
            str(cell.get("first_owner", "")),
            str(cell.get("holder_count", "")),
        ]
        for cell in cells
    ]
    return _table(headers, rows)


def render_elites(entries: list[dict[str, Any]]) -> str:
    """A table of elite-pool entries: ``rank | identity | solution |
    score`` (``solution`` a short digest, ``score`` rounded via ``_num``).
    A friendly one-line message instead of a bare header when
    ``entries == []``."""
    if not entries:
        return "no elites recorded yet."
    headers = ["rank", "identity", "solution", "score"]
    rows = [
        [
            str(entry.get("rank", "")),
            str(entry.get("identity", "")),
            _short_digest(str(entry.get("solution_digest", ""))),
            _num(entry.get("score", "")),
        ]
        for entry in entries
    ]
    return _table(headers, rows)


def render_board(entries: list[dict[str, Any]]) -> str:
    """A table of board entries, shape-aware over which metric produced
    them (``solution`` is always a short digest):

    - grading board (``asc_median_mean``/``mean`` aggregation -- entries
      carry ``ascensions``): ``rank | solution | owner | asc | median |
      mean`` (``median``/``mean`` via ``_num``).
    - coverage board (entries carry ``cells_held``): ``rank | solution |
      owner | cells``.
    - firsts board (entries carry ``firsts``): ``rank | solution | owner |
      firsts``.

    A friendly one-line message instead of a bare header when
    ``entries == []`` (there's no shape to detect from zero rows anyway --
    this also subsumes the old blank-column papercut, since a shape is
    now always resolved from real entries, never guessed)."""
    if not entries:
        return "no board entries yet."

    first = entries[0]
    if "ascensions" in first:
        headers = ["rank", "solution", "owner", "asc", "median", "mean"]
        rows = [
            [
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
                str(e.get("owner", "")),
                str(e.get("ascensions", "")),
                _num(e.get("median_progression", "")),
                _num(e.get("mean_progression", "")),
            ]
            for e in entries
        ]
    elif "cells_held" in first:
        headers = ["rank", "solution", "owner", "cells"]
        rows = [
            [
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
                str(e.get("owner", "")),
                str(e.get("cells_held", "")),
            ]
            for e in entries
        ]
    elif "firsts" in first:
        headers = ["rank", "solution", "owner", "firsts"]
        rows = [
            [
                str(e.get("rank", "")),
                _short_digest(str(e.get("solution_digest", ""))),
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
    """A table of registered solutions: ``solution | owner | repo | commit
    | registered`` (``solution``/``commit`` short digests). A friendly
    one-line message instead of a bare header when ``results == []``."""
    if not results:
        return "no solutions found."
    headers = ["solution", "owner", "repo", "commit", "registered"]
    rows = [
        [
            _short_digest(str(r.get("digest", ""))),
            str(r.get("owner", "")),
            str(r.get("repo", "")),
            _short_digest(str(r.get("commit_sha", ""))),
            str(r.get("registered_at", "")),
        ]
        for r in results
    ]
    return _table(headers, rows)


def render_show(solution: dict[str, Any]) -> str:
    """An aligned ``key: value`` block describing one registered solution
    (``digest``/``commit_sha`` shortened). A friendly one-line message
    instead of an empty block when ``solution`` is empty/missing."""
    if not solution:
        return "no such solution."
    preferred = ["digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at"]
    keys = [k for k in preferred if k in solution]
    keys += [k for k in solution if k not in preferred]
    width = max(len(k) for k in keys)
    lines = []
    for key in keys:
        value = solution[key]
        if key in ("digest", "commit_sha"):
            value = _short_digest(str(value))
        lines.append(f"{key:<{width}}: {value}")
    return "\n".join(lines)
