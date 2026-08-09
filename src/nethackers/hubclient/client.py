"""Hub HTTP client (M2a Task 13): a thin wrapper over ``httpx`` matching
Task 12's API surface (``nethackers.hub.api``) endpoint for endpoint -- one
method per read route plus ``register``, each building exactly the
URL/params/headers/body the server expects. ``http`` defaults to the real
``httpx`` module but is injectable, so ``tests/test_hubclient.py`` drives
every method against a fake recording calls -- no real network access
anywhere in this module.

``render_attainment``/``render_board`` are pure ASCII-table formatters over
the JSON a read call returns -- they don't touch ``HubClient`` at all, so
the CLI (``nethackers.cli``) can call them straight on whatever a
``HubClient`` method hands back.
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


def render_attainment(cells: list[dict[str, Any]]) -> str:
    """A text table of attainment cells: ``identity | milestone |
    first_owner | holders``, one row per cell (``holders`` <-
    ``cell["holder_count"]``). Always at least a header row, even for
    ``cells == []`` -- never raises on missing keys or empty input."""
    header = f"{'identity':<20} {'milestone':<12} {'first_owner':<16} {'holders':>7}"
    lines = [header]
    for cell in cells:
        lines.append(
            f"{str(cell.get('identity', '')):<20} {str(cell.get('milestone', '')):<12} "
            f"{str(cell.get('first_owner', '')):<16} {str(cell.get('holder_count', '')):>7}"
        )
    return "\n".join(lines)


def render_board(entries: list[dict[str, Any]]) -> str:
    """A text table of board entries: ``rank | solution | owner | asc |
    median | mean``, ``solution`` a 12-char ``solution_digest`` prefix.
    Grading-board entries (``asc_median_mean``/``mean`` aggregation) have
    all six fields; ``coverage``/``firsts`` entries carry fewer columns
    (``cells_held``/``firsts`` instead of ascensions/median/mean) -- those
    just render blank in the columns they don't have, never a crash.
    Always at least a header row, even for ``entries == []``."""
    header = f"{'rank':>4} {'solution':<14} {'owner':<16} {'asc':>4} {'median':>8} {'mean':>8}"
    lines = [header]
    for entry in entries:
        digest = str(entry.get("solution_digest", ""))[:12]
        lines.append(
            f"{str(entry.get('rank', '')):>4} {digest:<14} {str(entry.get('owner', '')):<16} "
            f"{str(entry.get('ascensions', '')):>4} {str(entry.get('median_progression', '')):>8} "
            f"{str(entry.get('mean_progression', '')):>8}"
        )
    return "\n".join(lines)
