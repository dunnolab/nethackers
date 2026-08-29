"""The achievements surface (renamed from 'attainment' -- the community
record of milestones reached). Reuses the existing attainment/coverage/firsts
readers and remaps their ``solution_digest``/``first_solution`` keys to the
opaque ``program_id``. DB tables keep the name ``attainment`` (storage != surface)."""
from __future__ import annotations

from typing import Any

from nethackers.hub.ids import program_id
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import read_attainment
from nethackers.hub.views.boards import coverage_board, firsts_board


def milestones(store: Store, identity: str | None = None) -> list[dict[str, Any]]:
    out = []
    for cell in read_attainment(store, identity=identity):
        row = dict(cell)
        row["first_program_id"] = program_id(row.pop("first_solution"))
        out.append(row)
    return out


def _remap_digest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        row = dict(r)
        row["program_id"] = program_id(row.pop("solution_digest"))
        out.append(row)
    return out


def coverage(store: Store) -> list[dict[str, Any]]:
    return _remap_digest(coverage_board(store))


def firsts(store: Store) -> list[dict[str, Any]]:
    return _remap_digest(firsts_board(store))
