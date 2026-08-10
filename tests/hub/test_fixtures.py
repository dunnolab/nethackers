"""Tests for ``nethackers.hub.fixtures``: the shared, deterministic demo
dataset (M2a Task 14) used both here and by ``create_default_app``'s
optional dev/demo seeding. See task-14-context.md, which governs:
``load_fixtures(store, now=...)`` is fully deterministic (a fixed default
``now``; every atom's progression is either a literal or an
``ACHIEVEMENTS[...]`` lookup), so ``GET /attainment`` and
``GET /board?objective=random`` over a freshly-loaded store always match
the committed snapshots in ``fixtures/hub/``.

Snapshot comparisons are normalized (sorted on a stable key) before
comparing -- not relying on dict/list/row order, per task-14-context.md.
Beyond the two snapshot-equality checks, this file asserts several
INDEPENDENT structural properties computed directly from the fixture's
known inputs (not merely echoing the snapshot back), so a broken
``load_fixtures``/``update_attainment``/``board`` couldn't accidentally
still satisfy a snapshot that was naively regenerated to match it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.fixtures import ALPHA, BETA, GAMMA, IDENTITY_A, IDENTITY_B, load_fixtures
from nethackers.hub.store import Store

REPO_ROOT = Path(__file__).parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "hub"


def _client(tmp_path: Any) -> TestClient:
    store = Store(tmp_path / "hub.db")
    store.init_schema()
    load_fixtures(store)
    app = create_app(store, LocalStubAuth({}))
    return TestClient(app)


def _load_snapshot(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


def _normalize_attainment(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(cells, key=lambda c: (c["identity"], c["milestone"]))


def _normalize_board(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda e: e["solution_digest"])


def test_attainment_matches_committed_snapshot(tmp_path: Any) -> None:
    client = _client(tmp_path)

    response = client.get("/attainment")

    assert response.status_code == 200
    live = _normalize_attainment(response.json())
    snapshot = _normalize_attainment(_load_snapshot("attainment.json"))
    assert live == snapshot


def test_board_random_matches_committed_snapshot(tmp_path: Any) -> None:
    client = _client(tmp_path)

    response = client.get("/board", params={"objective": "random"})

    assert response.status_code == 200
    live = _normalize_board(response.json())
    snapshot = _normalize_board(_load_snapshot("board_random.json"))
    assert live == snapshot


# --- Independent structural assertions (not snapshot echo) ----------------


def test_identity_a_dlvl10_cell_has_a_single_holder(tmp_path: Any) -> None:
    # ALPHA is the only solution that ever produces an atom on IDENTITY_A
    # (its own identity objective, plus "random"), so every lit IDENTITY_A
    # cell -- including the deepest one it reaches, Dlvl:10 -- has exactly
    # one holder: ALPHA/alice.
    client = _client(tmp_path)

    cells = client.get("/attainment", params={"identity": IDENTITY_A}).json()
    cell = next(c for c in cells if c["milestone"] == "Dlvl:10")

    assert cell["holder_count"] == 1
    assert cell["first_solution"] == ALPHA
    assert cell["first_owner"] == "alice"


def test_identity_b_ascend_cell_is_lit_only_by_the_ascended_solution(tmp_path: Any) -> None:
    # BETA's "random"-objective IDENTITY_B atom is the fixture's only
    # ascension (progression=1.0) -- GAMMA never gets remotely close (its
    # best IDENTITY_B atom is Dlvl:4), so "You ascend t" has exactly one
    # holder: BETA/bob.
    client = _client(tmp_path)

    cells = client.get("/attainment", params={"identity": IDENTITY_B}).json()
    cell = next(c for c in cells if c["milestone"] == "You ascend t")

    assert cell["holder_count"] == 1
    assert cell["first_solution"] == BETA
    assert cell["first_owner"] == "bob"


def test_identity_b_shallow_cell_is_held_by_both_gamma_and_beta(tmp_path: Any) -> None:
    # Dlvl:2 sits below both GAMMA's own Dlvl:4 atom and BETA's ascension
    # -- both solutions reach it, so (unlike the two single-holder cells
    # above) it's a genuine two-holder cell. GAMMA is listed first in
    # fixtures.py's atom list, so it wins the same-``now`` first-ratchet.
    client = _client(tmp_path)

    cells = client.get("/attainment", params={"identity": IDENTITY_B}).json()
    cell = next(c for c in cells if c["milestone"] == "Dlvl:2")

    assert cell["holder_count"] == 2
    assert cell["first_solution"] == GAMMA
    assert cell["first_owner"] == "carol"


def test_no_milestone_none_atom_leaks_into_attainment(tmp_path: Any) -> None:
    # GAMMA's second IDENTITY_B atom has milestone=None (a bot-failure /
    # no-progress episode) -- update_attainment must skip it outright, so
    # it can never surface as a lit cell.
    client = _client(tmp_path)

    cells = client.get("/attainment", params={"identity": IDENTITY_B}).json()

    assert cells != []
    assert all(c["milestone"] is not None for c in cells)


def test_board_random_ranks_the_ascended_solution_first(tmp_path: Any) -> None:
    # BETA has one ascension under "random"; ALPHA has none -- the
    # asc_median_mean aggregation must rank BETA first regardless of mean
    # progression. GAMMA has no "random" atoms at all, so it must not
    # appear here.
    client = _client(tmp_path)

    entries = client.get("/board", params={"objective": "random"}).json()

    assert [e["solution_digest"] for e in entries] == [BETA, ALPHA]
    assert entries[0]["ascensions"] == 1
    assert entries[1]["ascensions"] == 0
    assert GAMMA not in [e["solution_digest"] for e in entries]
