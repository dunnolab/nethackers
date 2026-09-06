"""The program registry surface: solutions rows re-presented with the opaque
``id`` and a ``reference {repo, commit}`` object. ``root``/``entrypoint`` are
intentionally omitted -- the arena hard-codes ``bot.py`` at the repo root."""
from __future__ import annotations

from typing import Any

from nethackers.hub.ids import program_id
from nethackers.hub.store import Store

_COLUMNS = ("digest", "repo", "commit_sha", "owner", "registered_at")


def _row(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": program_id(values["digest"]),
        "owner": values["owner"],
        "reference": {"repo": values["repo"], "commit": values["commit_sha"]},
        "registered_at": values["registered_at"],
    }


def _where(owner: str | None) -> tuple[str, list[Any]]:
    return (" WHERE owner = ?", [owner]) if owner is not None else ("", [])


def list_programs(store: Store, owner: str | None = None, limit: int = 50,
                  offset: int = 0) -> list[dict[str, Any]]:
    where, params = _where(owner)
    sql = (f"SELECT {', '.join(_COLUMNS)} FROM solutions{where}"
           " ORDER BY registered_at DESC LIMIT ? OFFSET ?")
    rows = store.conn.execute(sql, [*params, limit, offset]).fetchall()
    return [_row(dict(zip(_COLUMNS, r, strict=True))) for r in rows]


def count_programs(store: Store, owner: str | None = None) -> int:
    """How many programs match the same filter ``list_programs`` pages over.
    A page cannot report the size of its own set -- consumers that show a
    count (the website's hacker popup) need this, not ``len(rows)``."""
    where, params = _where(owner)
    return int(store.conn.execute(f"SELECT COUNT(*) FROM solutions{where}", params).fetchone()[0])


def get_program(store: Store, program_id_value: str) -> dict[str, Any] | None:
    digest = store.digest_for_program_id(program_id_value)
    if digest is None:
        return None
    row = store.get_solution(digest)
    if row is None:
        return None
    return _row(row)
